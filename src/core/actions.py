from contextlib import closing
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import time

from core import b2connect
from core.cache import cache_filepath, publish_cache, publish_thumbnail, staged_file
from core.config import settings
from core.controls import forget_missing, lease
from core.errors import AppError, BackendError
from core.media import IMAGE_EXTENSIONS, process_image
from core.state import connect, file_lock, file_record, transaction
from core.uploads import UPLOAD_CONCURRENCY
from misc import generate_random_string, wh_report
from werkzeug.utils import secure_filename


@dataclass(frozen=True)
class UploadResult:
    filename: str
    size: int


def copy_bounded(stream, destination, maximum):
    size = 0
    with destination.open("wb") as output:
        while chunk := stream.read(64 * 1024):
            size += len(chunk)
            if size > maximum:
                raise AppError("File exceeds the upload limit.", 413)
            output.write(chunk)
    return size


def recover_name(filename, row, digest):
    if row["status"] not in {"pending", "failed"}:
        raise AppError("Filename already exists or was previously used.", 409)
    if settings().use_b2_storage:
        remote = b2connect.b2_file_info(filename)
        if remote:
            matching = remote.file_info.get("bsxu_sha256") == digest
            with transaction() as connection:
                connection.execute(
                    "UPDATE uploaded_names SET status = 'available', deleted = 0, backed_up = 1, size = ? WHERE filename = ?",
                    (remote.size, filename),
                )
            if matching:
                forget_missing(filename)
                return UploadResult(filename, remote.size)
            raise AppError("An earlier upload used this filename. Its link has been recovered.", 409)
    target = cache_filepath(filename)
    if target.is_file():
        with target.open("rb") as stream:
            local_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        with transaction() as connection:
            connection.execute("UPDATE uploaded_names SET status = 'available', deleted = 0, cached = 1 WHERE filename = ?", (filename,))
        if local_digest == digest:
            return UploadResult(filename, target.stat().st_size)
        raise AppError("An earlier upload used this filename.", 409)
    with transaction() as connection:
        connection.execute("DELETE FROM uploaded_names WHERE filename = ? AND status IN ('pending', 'failed')", (filename,))
    return None


def upload_file(uploaded_file, custom_file_name=None, expiry_hours=None):
    config = settings()
    original_name = secure_filename(uploaded_file.filename or "")
    extension = Path(original_name).suffix.lower()
    if not extension:
        raise AppError("Filename must contain an extension.")
    custom = secure_filename(custom_file_name or "")
    if custom_file_name and (not custom or len(custom) > 150):
        raise AppError("Use a shorter filename containing letters, numbers, spaces, dots, dashes or underscores.")
    expires = None
    if expiry_hours:
        try:
            hours = int(expiry_hours)
        except (ValueError, TypeError) as exception:
            raise AppError("Expiry must be a whole number of hours.") from exception
        if not 1 <= hours <= 8760:
            raise AppError("Expiry must be between 1 and 8760 hours.")
        expires = time.time() + hours * 3600
    maximum = config.max_upload_size_mb * 1024 * 1024
    with lease("uploads", UPLOAD_CONCURRENCY), staged_file(maximum * 3) as temporary:
        size = copy_bounded(uploaded_file.stream, temporary, maximum)
        if not size:
            raise AppError("The selected file is empty.")
        thumbnail = None
        if extension in IMAGE_EXTENSIONS:
            temporary, thumbnail = process_image(temporary, extension)
            size = temporary.stat().st_size
        with temporary.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        for _ in range(10):
            filename = (custom or generate_random_string(config.name_length)) + extension
            with file_lock(filename):
                with closing(connect()) as connection:
                    row = connection.execute("SELECT * FROM uploaded_names WHERE filename = ?", (filename,)).fetchone()
                if row:
                    if custom:
                        recovered = recover_name(filename, row, digest)
                        if recovered:
                            return recovered
                    else:
                        continue
                if os.path.lexists(cache_filepath(filename)):
                    if custom:
                        raise AppError("Filename already exists.", 409)
                    continue
                if config.use_b2_storage:
                    remote = b2connect.b2_file_info(filename)
                    if remote:
                        b2connect.remember_remote(remote)
                        if custom:
                            raise AppError("Filename already exists in B2.", 409)
                        continue
                with transaction() as connection:
                    connection.execute(
                        "INSERT INTO uploaded_names (filename, backed_up, status, size, original_name, created, accessed, sha256, expires) "
                        "VALUES (?, 0, 'pending', ?, ?, ?, ?, ?, ?)",
                        (filename, size, original_name, time.time(), time.time(), digest, expires),
                    )
                try:
                    if config.use_b2_storage:
                        b2connect.b2_file_upload(temporary, filename, digest)
                        with transaction() as connection:
                            connection.execute("UPDATE uploaded_names SET status = 'available', backed_up = 1 WHERE filename = ?", (filename,))
                        try:
                            publish_cache(temporary, filename, size)
                        except (OSError, AppError) as exception:
                            wh_report("Upload stored in B2; local caching failed.", exception)
                    else:
                        publish_cache(temporary, filename, size)
                        with transaction() as connection:
                            connection.execute("UPDATE uploaded_names SET status = 'available' WHERE filename = ?", (filename,))
                    if thumbnail:
                        try:
                            publish_thumbnail(thumbnail, filename)
                        except OSError as exception:
                            wh_report("Thumbnail caching failed.", exception)
                    forget_missing(filename)
                    return UploadResult(filename, size)
                except Exception:
                    with transaction() as connection:
                        connection.execute("UPDATE uploaded_names SET status = 'failed' WHERE filename = ? AND status = 'pending'", (filename,))
                    raise
        raise BackendError("Unable to generate an unused filename. Please retry.")


def recover_pending():
    with closing(connect()) as connection:
        rows = connection.execute("SELECT * FROM uploaded_names WHERE status IN ('pending', 'failed') LIMIT 100").fetchall()
    for row in rows:
        try:
            with file_lock(row["filename"]):
                current = file_record(row["filename"])
                if current and current["status"] in {"pending", "failed"}:
                    recover_name(current["filename"], current, current["sha256"])
        except AppError as exception:
            wh_report("Upload recovery could not finish.", exception)

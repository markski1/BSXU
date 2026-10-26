from contextlib import closing, contextmanager
import hashlib
import os
import secrets
import shutil
import time

from werkzeug.utils import secure_filename

from core.config import settings
from core.errors import AppError
from core.state import connect, file_lock, transaction
from misc import wh_report


def cache_filepath(filename):
    if not filename or len(filename) > 200 or secure_filename(filename) != filename:
        raise AppError("Invalid filename.")
    root = settings().cache_folder
    target = root / filename
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise AppError("Filename must stay inside the cache.")
    return target


def init_cache():
    config = settings()
    config.cache_folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = config.cache_folder / ".staging"
    if staging.is_symlink():
        raise RuntimeError("The staging directory must not be a symbolic link.")
    staging.mkdir(exist_ok=True, mode=0o700)
    thumbnails = config.state_folder / "thumbnails"
    if thumbnails.is_symlink():
        raise RuntimeError("The thumbnail directory must not be a symbolic link.")
    thumbnails.mkdir(exist_ok=True, mode=0o700)
    recover_staging()
    reconcile_cache()
    cleanup_deleted()


def recover_staging():
    root = settings().cache_folder / ".staging"
    with closing(connect()) as connection:
        stale = connection.execute("SELECT id FROM staging WHERE expires < ?", (time.time(),)).fetchall()
    for row in stale:
        target = root / row["id"]
        if target.is_symlink() or target.resolve().parent != root.resolve():
            continue
        try:
            if target.exists():
                shutil.rmtree(target)
        except FileNotFoundError:
            pass
        except OSError as exception:
            wh_report("Temporary file cleanup will be retried.", exception)
            continue
        with transaction() as connection:
            connection.execute("DELETE FROM staging WHERE id = ?", (row["id"],))
    for target in root.iterdir():
        try:
            if target.is_file() and target.suffix == ".part" and target.stat().st_mtime < time.time() - 7200:
                target.unlink(missing_ok=True)
        except FileNotFoundError:
            pass


def reconcile_cache():
    files = []
    for target in settings().cache_folder.iterdir():
        if target.name.startswith(".") or not target.is_file() or target.is_symlink():
            continue
        try:
            cache_filepath(target.name)
            stat = target.stat()
            files.append((target.name, stat.st_size, stat.st_mtime))
        except (OSError, AppError):
            continue
    with transaction() as connection:
        for name, size, modified in files:
            connection.execute(
                "INSERT INTO uploaded_names (filename, backed_up, size, cached, original_name, created, accessed) "
                "VALUES (?, 0, ?, 1, ?, ?, ?) ON CONFLICT(filename) DO UPDATE SET "
                "cached = 1, size = excluded.size",
                (name, size, name, modified, modified),
            )
    with closing(connect()) as connection:
        cached = connection.execute("SELECT filename FROM uploaded_names WHERE cached = 1").fetchall()
    for row in cached:
        try:
            with file_lock(row["filename"]), transaction() as connection:
                if not cache_filepath(row["filename"]).is_file():
                    connection.execute("UPDATE uploaded_names SET cached = 0 WHERE filename = ?", (row["filename"],))
        except (AppError, OSError) as exception:
            wh_report("Cache reconciliation will be retried.", exception)


@contextmanager
def staged_file(reserve_bytes):
    config = settings()
    token = secrets.token_hex(16)
    directory = config.cache_folder / ".staging" / token
    with transaction() as connection:
        used = connection.execute("SELECT COALESCE(sum(bytes), 0) FROM staging").fetchone()[0]
        if used + reserve_bytes > config.staging_max_size_mb * 1024 * 1024:
            raise AppError("Temporary storage is busy. Please retry.", 503)
        if shutil.disk_usage(config.cache_folder).free < reserve_bytes + config.min_free_disk_mb * 1024 * 1024:
            raise AppError("Insufficient free disk space.", 507)
        connection.execute("INSERT INTO staging VALUES (?, ?, ?)", (token, reserve_bytes, time.time() + 7200))
    try:
        directory.mkdir(mode=0o700)
        yield directory / "upload.part"
    finally:
        resolved = directory.resolve()
        if resolved.parent != (config.cache_folder / ".staging").resolve():
            raise RuntimeError("Invalid staging path.")
        try:
            if resolved.exists():
                shutil.rmtree(resolved)
        except OSError as exception:
            with transaction() as connection:
                connection.execute("UPDATE staging SET expires = 0 WHERE id = ?", (token,))
            wh_report("Temporary file cleanup will be retried.", exception)
        else:
            with transaction() as connection:
                connection.execute("DELETE FROM staging WHERE id = ?", (token,))


def cached_size(connection):
    return connection.execute("SELECT COALESCE(sum(size), 0) FROM uploaded_names WHERE cached = 1").fetchone()[0]


def make_cache_room(file_size, connection, allow_eviction):
    capacity = settings().cache_max_size_mb * 1024 * 1024
    if file_size > capacity:
        raise AppError("File exceeds the configured cache capacity.", 507)
    size = cached_size(connection)
    if size + file_size <= capacity:
        return
    if allow_eviction:
        rows = connection.execute(
            "SELECT filename, size FROM uploaded_names WHERE cached = 1 AND backed_up = 1 "
            "AND status = 'available' ORDER BY accessed, filename"
        ).fetchall()
        for row in rows:
            cache_filepath(row["filename"]).unlink(missing_ok=True)
            connection.execute("UPDATE uploaded_names SET cached = 0 WHERE filename = ?", (row["filename"],))
            size -= row["size"]
            if size + file_size <= capacity:
                return
    raise AppError("Storage is full. Remove files or increase the cache capacity.", 507)


def publish_cache(temporary_path, filename, size):
    with transaction() as connection:
        make_cache_room(size, connection, settings().use_b2_storage)
        os.replace(temporary_path, cache_filepath(filename))
        connection.execute("UPDATE uploaded_names SET cached = 1 WHERE filename = ?", (filename,))


def cache_delete_file(filename):
    with file_lock(filename):
        with transaction() as connection:
            row = connection.execute("SELECT backed_up FROM uploaded_names WHERE filename = ?", (filename,)).fetchone()
            if not row:
                raise AppError("File not found.", 404)
            primary = not (settings().use_b2_storage and row["backed_up"])
            if primary:
                connection.execute("UPDATE uploaded_names SET status = 'deleted', deleted = 1 WHERE filename = ?", (filename,))
        try:
            cache_filepath(filename).unlink(missing_ok=True)
            with transaction() as connection:
                connection.execute("UPDATE uploaded_names SET cached = 0 WHERE filename = ?", (filename,))
                delete_thumbnail(filename, connection)
        except OSError as exception:
            wh_report("Local deletion needs cleanup.", exception)
            raise AppError("Link disabled; local cleanup needs retrying with maintenance." if primary else "Could not remove this cached copy. Please retry.", 503) from exception


def cleanup_deleted():
    with closing(connect()) as connection:
        rows = connection.execute("SELECT filename FROM uploaded_names WHERE status = 'deleted' AND cached = 1 LIMIT 1000").fetchall()
    for row in rows:
        try:
            with file_lock(row["filename"]):
                cache_filepath(row["filename"]).unlink(missing_ok=True)
                with transaction() as connection:
                    connection.execute("UPDATE uploaded_names SET cached = 0 WHERE filename = ?", (row["filename"],))
                    delete_thumbnail(row["filename"], connection)
        except (OSError, AppError) as exception:
            wh_report("Deleted file cleanup will be retried.", exception)


def clear_cache(filenames):
    for name in filenames:
        cache_delete_file(name)


def thumbnail_path(filename):
    return settings().state_folder / "thumbnails" / (hashlib.sha256(filename.encode()).hexdigest() + ".png")


def delete_thumbnail(filename, connection):
    thumbnail_path(filename).unlink(missing_ok=True)
    connection.execute("DELETE FROM thumbnails WHERE filename = ?", (filename,))


def publish_thumbnail(source, filename):
    size = source.stat().st_size
    with transaction() as connection:
        total = connection.execute("SELECT COALESCE(sum(size), 0) FROM thumbnails").fetchone()[0]
        for row in connection.execute("SELECT filename, size FROM thumbnails ORDER BY created").fetchall():
            if total + size <= 32 * 1024 * 1024:
                break
            delete_thumbnail(row["filename"], connection)
            total -= row["size"]
        os.replace(source, thumbnail_path(filename))
        connection.execute("INSERT OR REPLACE INTO thumbnails VALUES (?, ?, ?)", (filename, size, time.time()))

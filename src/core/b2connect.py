from contextlib import closing
from dataclasses import dataclass
import threading
import time

from b2sdk.v2 import B2Api, InMemoryAccountInfo
from b2sdk.v2.exception import FileNotPresent
from flask import current_app

from core.cache import cache_filepath, delete_thumbnail, publish_cache, staged_file
from core.config import settings
from core.controls import forget_missing, mark_missing, missing
from core.errors import AppError, BackendError, NotFound
from core.state import connect, file_lock, file_record, transaction
from misc import wh_report


@dataclass(frozen=True)
class RemoteFile:
    name: str
    size: int
    file_info: dict
    modified: float


def init_app(app):
    app.extensions["b2_lock"] = threading.Lock()


def bucket():
    if not settings().use_b2_storage:
        raise BackendError("B2 storage is disabled.")
    if "b2_bucket" not in current_app.extensions:
        with current_app.extensions["b2_lock"]:
            if "b2_bucket" not in current_app.extensions:
                try:
                    api = B2Api(InMemoryAccountInfo(), max_upload_workers=2, max_download_workers=2, max_download_streams_per_file=1)
                    api.authorize_account("production", settings().b2_key_id, settings().b2_app_key)
                    current_app.extensions["b2_bucket"] = api.get_bucket_by_name(settings().b2_bucket_name)
                except Exception as exception:
                    raise BackendError() from exception
    return current_app.extensions["b2_bucket"]


def b2_file_info(filename):
    if missing(filename):
        return None
    try:
        info = bucket().get_file_info_by_name(filename)
        return RemoteFile(filename, info.content_length, info.file_info, info.upload_timestamp / 1000)
    except FileNotPresent:
        mark_missing(filename)
        return None
    except BackendError:
        raise
    except Exception as exception:
        raise BackendError() from exception


def b2_file_upload(filepath, filename, digest):
    try:
        return bucket().upload_local_file(
            local_file=str(filepath), file_name=filename,
            content_type="application/octet-stream", file_infos={"bsxu_sha256": digest},
        )
    except Exception as exception:
        forget_missing(filename)
        raise BackendError("B2 did not confirm this upload. Retry the same filename to recover it safely.") from exception


def remember_remote(remote):
    with transaction() as connection:
        connection.execute(
            "INSERT INTO uploaded_names (filename, backed_up, size, original_name, created, accessed) VALUES (?, 1, ?, ?, ?, ?) "
            "ON CONFLICT(filename) DO UPDATE SET backed_up = 1, size = excluded.size",
            (remote.name, remote.size, remote.name, remote.modified, time.time()),
        )


def b2_cache_file(filename, remote):
    with file_lock(filename):
        row = file_record(filename)
        if row and row["status"] != "available":
            raise NotFound()
        target = cache_filepath(filename)
        if target.is_file():
            return target
        download = None
        try:
            with staged_file(remote.size) as temporary:
                download = bucket().download_file_by_name(file_name=filename)
                with temporary.open("wb") as output:
                    size = 0
                    for chunk in download.response.iter_content(64 * 1024):
                        size += len(chunk)
                        if size > remote.size:
                            raise BackendError("B2 returned more bytes than expected.")
                        output.write(chunk)
                if size != remote.size:
                    raise BackendError("B2 returned an incomplete file.")
                remember_remote(remote)
                publish_cache(temporary, filename, size)
            return target
        except FileNotPresent as exception:
            mark_missing(filename)
            raise NotFound() from exception
        except AppError:
            raise
        except Exception as exception:
            raise BackendError() from exception
        finally:
            if download is not None:
                download.response.close()


def b2_get_files(cursor=None, prefix=""):
    try:
        target = bucket()
        result = target.api.session.list_file_names(
            target.id_, start_file_name=cursor, max_file_count=51, prefix=prefix or None,
        )
        files = result["files"]
        next_cursor = files[50]["fileName"] if len(files) > 50 else None
        for info in files[:50]:
            name = info["fileName"]
            try:
                cache_filepath(name)
            except Exception:
                continue
            remember_remote(RemoteFile(name, info.get("size", info.get("contentLength", 0)), info.get("fileInfo", {}), info["uploadTimestamp"] / 1000))
        return next_cursor
    except Exception as exception:
        raise BackendError("Could not synchronize B2 files. Existing local records remain available.") from exception


def b2_delete_file(filename):
    with file_lock(filename):
        with transaction() as connection:
            connection.execute("UPDATE uploaded_names SET status = 'deleting' WHERE filename = ?", (filename,))
        try:
            target = bucket()
            versions = list(target.list_file_versions(filename))
            for version in reversed(versions):
                target.delete_file_version(version.id_, filename)
            with transaction() as connection:
                connection.execute("UPDATE uploaded_names SET status = 'deleted', deleted = 1 WHERE filename = ?", (filename,))
        except Exception as exception:
            raise BackendError("Link disabled. B2 deletion is pending; retry Delete file to finish.") from exception
        try:
            with transaction() as connection:
                cache_filepath(filename).unlink(missing_ok=True)
                connection.execute("UPDATE uploaded_names SET cached = 0 WHERE filename = ?", (filename,))
                delete_thumbnail(filename, connection)
            forget_missing(filename)
        except OSError as exception:
            wh_report("Cloud deletion completed; local cleanup is pending.", exception)
            raise BackendError("Link disabled and B2 versions deleted; run maintenance to retry local cleanup.") from exception

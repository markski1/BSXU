from typing import Optional
from contextlib import closing

from b2sdk.v2 import B2Api, FileVersion, InMemoryAccountInfo
from b2sdk.v2.exception import FileNotPresent
import os

from core.cache import cache_filepath, has_cache_capacity, make_cache_room, staged_file
from core.state import connect, file_lock, storage_transaction
from core.config import use_b2_storage, b2_app_id, b2_app_key, b2_bucket_name
from misc import wh_report

if use_b2_storage:
    info = InMemoryAccountInfo()
    b2_api = B2Api(info)
    b2_api.authorize_account("production", b2_app_id, b2_app_key)
    b2_bucket = b2_api.get_bucket_by_name(b2_bucket_name)


def b2_file_upload(filepath: str, filename: str | None = None) -> bool:
    filename = filename or str(os.path.basename(filepath))

    try:
        b2_bucket.upload_local_file(local_file=filepath, file_name=filename)
        return True
    except Exception as e:
        wh_report("Error uploading file to B2.", e)
        return False


def b2_file_exists(filename: str) -> bool:
    return next(b2_bucket.list_file_versions(filename, fetch_count=1), None) is not None


class LimitedWriter:
    def __init__(self, stream, limit):
        self.stream = stream
        self.limit = limit
        self.bytes_written = 0

    def write(self, data):
        if self.bytes_written + len(data) > self.limit:
            raise ValueError("B2 download exceeds its declared size.")
        written = self.stream.write(data)
        self.bytes_written += written
        return written

    def seekable(self):
        return False


def b2_cache_file(filename: str) -> Optional[str]:
    """
    Downloads a file from B2 into the cache.
    :param filename: The name of the file to download.
    :return: Filepath the file was downloaded to. None if there was a failure.
    """

    file_download = None
    try:
        filepath = cache_filepath(filename)
        with file_lock(filename):
            with closing(connect("storage.sqlite3")) as connection:
                deleted = connection.execute(
                    "SELECT deleted FROM uploaded_names WHERE filename = ?", (filename,)
                ).fetchone()
            if deleted and deleted[0]:
                return None
            if filepath.is_file():
                return str(filepath)
            file_download = b2_bucket.download_file_by_name(file_name=filename)
            file_size = file_download.download_version.content_length
            with closing(connect("storage.sqlite3")) as connection:
                if file_size < 0 or not has_cache_capacity(file_size, connection, True):
                    return None
            with staged_file() as temporary_path:
                with temporary_path.open("wb") as stream:
                    writer = LimitedWriter(stream, file_size)
                    file_download.save(writer, allow_seeking=False)
                    if writer.bytes_written != file_size:
                        raise ValueError("Incomplete B2 download.")
                with storage_transaction() as connection:
                    make_cache_room(file_size, connection, True)
                    os.replace(temporary_path, filepath)
                    connection.execute(
                        "INSERT INTO uploaded_names (filename, backed_up) VALUES (?, 1) "
                        "ON CONFLICT(filename) DO UPDATE SET backed_up = 1", (filename,)
                    )
            return str(filepath)
    except FileNotPresent:
        return None
    except Exception as exception:
        wh_report("Error caching file from B2.", exception)
        return None
    finally:
        if file_download is not None:
            file_download.response.close()


def b2_get_files() -> list[FileVersion]:
    b2_list_gen = b2_bucket.ls(
        latest_only=True,
        fetch_count=10000
    )

    file_list = []
    for file_info, _ in b2_list_gen:
        file_list.append(file_info)

    return file_list


def b2_delete_file(filename: str) -> bool:
    try:
        filepath = cache_filepath(filename)
        cache_error = None
        with file_lock(filename):
            versions = list(b2_bucket.list_file_versions(filename))
            if not versions:
                return False
            for version in reversed(versions):
                b2_bucket.delete_file_version(version.id_, filename)
            with storage_transaction() as connection:
                connection.execute(
                    "INSERT INTO uploaded_names (filename, backed_up, deleted) VALUES (?, 1, 1) "
                    "ON CONFLICT(filename) DO UPDATE SET deleted = 1", (filename,)
                )
                try:
                    filepath.unlink(missing_ok=True)
                except OSError as exception:
                    cache_error = exception
        if cache_error is not None:
            wh_report("Error removing the deleted file from cache.", cache_error)
        return True
    except Exception as e:
        wh_report("Error deleting file from B2.", e)
        return False

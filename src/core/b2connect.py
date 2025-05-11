from typing import Optional

from b2sdk.v2 import *
import os

from core.cache import get_size, clear_cache
from core.config import cache_folder, use_b2_storage, b2_app_id, b2_app_key, b2_bucket_name, cache_max_size_mb
from misc import wh_report

if use_b2_storage:
    info = InMemoryAccountInfo()
    b2_api = B2Api(info)
    b2_api.authorize_account("production", b2_app_id, b2_app_key)
    b2_bucket = b2_api.get_bucket_by_name(b2_bucket_name)


def b2_file_upload(filepath: str) -> bool:
    filename = str(os.path.basename(filepath))

    try:
        with open(filepath, 'rb') as file:
            b2_bucket.upload_bytes(
                file_name=filename,
                data_bytes=file.read()
            )

        return True
    except Exception as e:
        wh_report("Error uploading file to B2.", e)
        return False


def b2_cache_file(filename: str) -> Optional[str]:
    """
    Downloads a file from B2 into the cache.
    :param filename: The name of the file to download.
    :return: Filepath the file was downloaded to. None if there was a failure.
    """

    # First, check if the cache should be cleared
    cache_size = get_size(cache_folder)
    if int((cache_size / 1024) / 1024) >= cache_max_size_mb:
        clear_cache(cache_folder)

    filepath = str(os.path.join(cache_folder, filename))

    # An exception indicates the file does not exist.
    # There is an API call to check for existence first, but it is a metered operation.
    try:
        file_download = b2_bucket.download_file_by_name(file_name=filename)
        file_download.save_to(filepath, 'wb')

        return filepath
    except:
        return None


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
        result = b2_bucket.get_file_info_by_name(filename)
        if not result:
            return False

        b2_bucket.delete_file_version(result.id_, filename)
        return True
    except Exception as e:
        wh_report("Error deleting file from B2.", e)
        return False

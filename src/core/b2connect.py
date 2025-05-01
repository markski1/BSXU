import shutil
from typing import Optional

from b2sdk.v2 import *
import os

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


def get_size(filepath: str) -> int:
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(filepath):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            # skip if symbolic link
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)

    return total_size


def clear_cache(path: str) -> None:
    files = [os.path.join(cache_folder, filename) for filename in os.listdir(path)]
    for filename in files:
        try:
            shutil.rmtree(filename)
        except Exception as e:
            wh_report(f"Exception found when attempting to clear the cache. File being deleted: {filename}", e)
            break

    wh_report("Cache has been cleared.")

import shutil

import b2sdk
from b2sdk.v2 import *
import os

from core.config import cache_folder, use_b2_storage, b2_app_id, b2_app_key, b2_bucket_name, cache_max_size_mb

if use_b2_storage:
    info = InMemoryAccountInfo()
    b2_api = B2Api(info)
    b2_api.authorize_account("production", b2_app_id, b2_app_key)
    b2_bucket = b2_api.get_bucket_by_name(b2_bucket_name)


def b2_file_upload(filepath):
    filename = str(os.path.basename(filepath))

    try:
        with open(filepath, 'rb') as file:
            b2_bucket.upload_bytes(
                file_name=filename,
                data_bytes=file.read()
            )

        return True
    except Exception as e:
        print(f"Exception found when uploading '{filename}': \n {e}")
        return False


def b2_cache_file(filename):
    # First, check if cache should be cleared
    cache_size = get_size(cache_folder)
    if int((cache_size / 1024) / 1024) >= cache_max_size_mb:
        clear_cache(cache_folder)

    filepath = str(os.path.join(cache_folder, filename))

    try:
        file_download = b2_bucket.download_file_by_name(file_name=filename)
        file_download.save_to(filepath, 'wb')

        return filepath

    except b2sdk.exception.FileNotPresent:
        return False

    except FileNotFoundError:
        return False

    except Exception as e:
        print(f"Exception found when caching '{filename}': \n {e}")
        return False


def get_size(path):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            # skip if symbolic link
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)

    return total_size


def clear_cache(path):
    files = [os.path.join(cache_folder, filename) for filename in os.listdir(path)]
    for filename in files:
        try:
            shutil.rmtree(filename)
        except Exception as e:
            print(f"Exception found when attempting to clear the cache.")
            print(f"File being deleted: {filename}")
            print(f"Exception: {e}")
            break

    print("Cache has been cleared.")

from b2sdk.exception import *
from b2sdk.v2 import *
import os

from core.config import cache_folder, b2_app_id, b2_app_key, b2_bucket_name

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
    filepath = str(os.path.join(cache_folder, filename))

    try:
        file_download = b2_bucket.download_file_by_name(file_name=filename)
        file_download.save_to(filepath, 'wb')

        return filepath

    except FileNotPresent:
        return False

    except FileNotFoundError:
        return False

    except Exception as e:
        print(f"Exception found when caching '{filename}': \n {e}")
        return False

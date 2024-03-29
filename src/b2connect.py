from b2sdk.exception import *
from b2sdk.v2 import *
import os
from dotenv import load_dotenv

load_dotenv()

cache_folder = os.getenv('CACHE_FOLDER')

b2_app_id = os.getenv('B2_KEY_ID')
b2_app_key = os.getenv('B2_APP_KEY')

info = InMemoryAccountInfo()
b2_api = B2Api(info)
b2_api.authorize_account("production", b2_app_id, b2_app_key)
b2_bucket = b2_api.get_bucket_by_name(os.getenv('B2_BUCKET_NAME'))


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


def b2_file_exists(filename):
    try:
        b2_bucket.get_file_info_by_name(str(filename))
        return True
    except FileNotPresent:
        return False


def b2_cache_file(filename):
    filepath = os.path.join(cache_folder, filename)

    try:
        with open(filepath, 'wb') as file:
            b2_bucket.download_file_by_name(str(filename), file)

        return filepath

    except Exception as e:
        print(f"Exception found when uploading '{filename}': \n {e}")
        return False

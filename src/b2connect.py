from b2sdk.exception import *
from b2sdk.v2 import *
import os
from dotenv import load_dotenv

load_dotenv()

cache_folder = os.getenv('CACHE_FOLDER')

b2_auth_key = os.getenv('B2_KEY')
b2_app_key = os.getenv('B2_APP_KEY')

info = InMemoryAccountInfo()
b2_api = B2Api(info)
b2_api.authorize_account("production", b2_auth_key, b2_app_key)
b2_bucket = b2_api.get_bucket_by_name(os.getenv('B2_BUCKET_NAME'))


def b2_file_upload(filepath):
    filename = os.path.basename(filepath)

    try:
        with open(filepath, 'rb') as file:
            bucket.upload_bytes(filename, file.read())

        return True
    except Exception as e:
        print(f"Exception found when uploading '{filename}': \n {e}")
        return False


def b2_file_exists(filename):
    try:
        bucket.get_file_info(filename)
        return True
    except FileNotPresent:
        return False


def b2_cache_file(filename):
    filepath = os.path.join(cache_folder, filename)

    try:
        with open(filepath, 'wb') as file:
            bucket.download_file_by_name(filename, file)

        return filepath

    except Exception as e:
        print(f"Exception found when uploading '{filename}': \n {e}")
        return False

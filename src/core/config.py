import os
from dotenv import load_dotenv
from flask import Flask

load_dotenv()

authkey = os.getenv('AUTH_KEY')
url_path = os.getenv('URL_PATH')
name_length = os.getenv('NAME_LENGTH')

cache_folder = os.getenv('CACHE_FOLDER')
os.makedirs(cache_folder, exist_ok=True)

b2_app_id = os.getenv('B2_KEY_ID')
b2_app_key = os.getenv('B2_APP_KEY')
b2_bucket_name = os.getenv('B2_BUCKET_NAME')

app_host = os.getenv('APP_HOST')
app_port = os.getenv('APP_PORT')
app_debug = os.getenv('APP_DEBUG')

app = Flask(__name__,
            static_folder="../webpanel/static",
            template_folder="../webpanel/templates")

import os
from dotenv import load_dotenv
from flask import Flask
from core import session

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
cache_max_size_mb = int(os.getenv('CACHE_MAX_SIZE_MB'))

discord_webhook = os.getenv('DISCORD_WEBHOOK')
using_discord_wb = discord_webhook.lower() != "false"


panel_enabled = os.getenv('PANEL_ENABLED') == 'true'
use_b2_storage = os.getenv('USE_B2_STORAGE') == 'true'

app = Flask(__name__,
            static_folder="../webpanel/static",
            template_folder="../webpanel/templates")

session.init_app(app)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')

import os

from dotenv import load_dotenv
from flask import Flask

from core import session

load_dotenv()


def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _get_required_int(name: str) -> int:
    value = _get_required_env(name)
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer.") from exc


def _get_positive_int(name: str, default: int | None = None) -> int:
    value = os.getenv(name)
    if value is None and default is not None:
        result = default
    else:
        result = _get_required_int(name)

    if result <= 0:
        raise RuntimeError(f"Environment variable {name} must be greater than zero.")
    return result


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() == "true"


authkey = _get_required_env("AUTH_KEY")
url_path = _get_required_env("URL_PATH")
name_length = _get_required_int("NAME_LENGTH")

cache_folder = _get_required_env("CACHE_FOLDER")
os.makedirs(cache_folder, exist_ok=True)

app_host = os.getenv("APP_HOST", "127.0.0.1")
app_port = _get_required_int("APP_PORT")
app_debug = _get_bool("APP_DEBUG")
if app_debug and app_host not in {"127.0.0.1", "::1", "localhost"}:
    raise RuntimeError("APP_DEBUG requires a loopback APP_HOST.")
cache_max_size_mb = _get_positive_int("CACHE_MAX_SIZE_MB")
max_upload_size_mb = _get_positive_int(
    "MAX_UPLOAD_SIZE_MB", default=cache_max_size_mb
)

panel_enabled = _get_bool("PANEL_ENABLED")
use_b2_storage = _get_bool("USE_B2_STORAGE")

b2_app_id = _get_required_env("B2_KEY_ID") if use_b2_storage else None
b2_app_key = _get_required_env("B2_APP_KEY") if use_b2_storage else None
b2_bucket_name = _get_required_env("B2_BUCKET_NAME") if use_b2_storage else None

app = Flask(
    __name__,
    static_folder="../webpanel/static",
    template_folder="../webpanel/templates",
)
app.jinja_env.autoescape = True

if panel_enabled:
    app.config["SECRET_KEY"] = _get_required_env("SECRET_KEY")
else:
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

app.config["MAX_CONTENT_LENGTH"] = (max_upload_size_mb + 1) * 1024 * 1024
app.config["MAX_FORM_PARTS"] = 20

session.init_app(app)

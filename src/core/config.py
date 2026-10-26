import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from flask import current_app


@dataclass(frozen=True)
class Settings:
    admin_key: str
    secret_key: str
    url_path: str
    cache_folder: Path
    state_folder: Path
    use_b2_storage: bool
    panel_enabled: bool
    cookie_secure: bool
    trust_proxy_headers: bool
    name_length: int
    cache_max_size_mb: int
    max_upload_size_mb: int
    staging_max_size_mb: int
    min_free_disk_mb: int
    image_max_pixels: int
    image_max_frames: int
    image_timeout: int
    b2_key_id: str
    b2_app_key: str
    b2_bucket_name: str
    webhook_url: str
    webhook_param: str

    @classmethod
    def load(cls, overrides=None):
        if overrides is None:
            load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        values = dict(os.environ)
        values.update(overrides or {})

        def text(name, default=""):
            return str(values.get(name, default)).strip()

        def positive(name, default, minimum=1):
            try:
                value = int(values.get(name, default))
            except (ValueError, TypeError) as exception:
                raise RuntimeError(f"{name} must be an integer.") from exception
            if value < minimum:
                raise RuntimeError(f"{name} must be at least {minimum}.")
            return value

        def boolean(name, default):
            value = text(name, str(default)).lower()
            if value not in {"true", "false"}:
                raise RuntimeError(f"{name} must be true or false.")
            return value == "true"

        def secret(name, required=True):
            value = text(name)
            if required and (len(value) < 32 or value.lower().startswith("changeme")):
                raise RuntimeError(f"Set {name} to a unique random secret of at least 32 characters.")
            return value

        def validate_url(value, schemes, name):
            if any(character.isspace() for character in value):
                raise RuntimeError(f"{name} must not contain unescaped whitespace.")
            try:
                parsed = urlsplit(value)
                port = parsed.port
            except ValueError as exception:
                raise RuntimeError(f"{name} must contain a valid hostname and port.") from exception
            if (parsed.scheme not in schemes or not parsed.hostname or port == 0
                    or parsed.username is not None or parsed.password is not None or parsed.fragment):
                raise RuntimeError(f"{name} must be a valid URL without credentials or a fragment.")
            return parsed

        panel = boolean("PANEL_ENABLED", True)
        if text("LEGACY_UPLOAD_MAX_MB", "0") not in {"", "0"}:
            raise RuntimeError("Form-key uploads are no longer supported. Use an authentication header.")
        admin_key = secret("ADMIN_KEY", panel)
        if len(admin_key) > 256:
            raise RuntimeError("ADMIN_KEY must not exceed 256 characters.")
        url = text("URL_PATH")
        parts = validate_url(url, {"http", "https"}, "URL_PATH")
        if parts.path not in {"", "/"} or parts.query:
            raise RuntimeError("URL_PATH must use the root of a dedicated host without a query.")
        application_folder = Path(__file__).resolve().parents[1]
        cache = (application_folder / text("CACHE_FOLDER", "cache")).resolve()
        state = (application_folder / text("STATE_FOLDER", "instance")).resolve()
        if state.is_relative_to(cache) or cache.is_relative_to(state):
            raise RuntimeError("STATE_FOLDER and CACHE_FOLDER must be separate directories.")
        upload_limit = positive("MAX_UPLOAD_SIZE_MB", 128)
        b2 = boolean("USE_B2_STORAGE", False)
        b2_values = [text(name) for name in ("B2_KEY_ID", "B2_APP_KEY", "B2_BUCKET_NAME")]
        if b2 and not all(b2_values):
            raise RuntimeError("B2_KEY_ID, B2_APP_KEY and B2_BUCKET_NAME are required for B2.")
        webhook = text("WEBHOOK_URL", "false")
        if webhook != "false":
            validate_url(webhook, {"https"}, "WEBHOOK_URL")
        name_length = positive("NAME_LENGTH", 12, 12)
        if name_length > 150:
            raise RuntimeError("NAME_LENGTH must not exceed 150 characters.")
        return cls(
            admin_key=admin_key,
            secret_key=secret("SECRET_KEY"),
            url_path=url.rstrip("/") + "/",
            cache_folder=cache,
            state_folder=state,
            use_b2_storage=b2,
            panel_enabled=panel,
            cookie_secure=boolean("SESSION_COOKIE_SECURE", True),
            trust_proxy_headers=boolean("TRUST_PROXY_HEADERS", False),
            name_length=name_length,
            cache_max_size_mb=positive("CACHE_MAX_SIZE_MB", 2048),
            max_upload_size_mb=upload_limit,
            staging_max_size_mb=positive("STAGING_MAX_SIZE_MB", upload_limit * 8 + 8),
            min_free_disk_mb=positive("MIN_FREE_DISK_MB", 64, 0),
            image_max_pixels=positive("IMAGE_MAX_PIXELS", 40_000_000),
            image_max_frames=positive("IMAGE_MAX_FRAMES", 100),
            image_timeout=positive("IMAGE_TIMEOUT_SECONDS", 20),
            b2_key_id=b2_values[0],
            b2_app_key=b2_values[1],
            b2_bucket_name=b2_values[2],
            webhook_url=webhook,
            webhook_param=text("WEBHOOK_PARAM", "content"),
        )


def settings() -> Settings:
    return current_app.extensions["settings"]

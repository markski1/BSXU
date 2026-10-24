import math
import re
import secrets
import time

from flask import g, request, session
from flask_login import current_user

from core.errors import AppError
from core.session import hash_token, upload_authorized
from core.state import transaction

UPLOAD_ENDPOINTS = {"files.upload_file", "panel.file_upload"}
UPLOAD_CONCURRENCY = 2
MAX_BATCH_FILES = 5


def require_upload_key():
    authorization = request.headers.get("Authorization")
    alternate = request.headers.get("X-Upload-Key")
    if authorization is not None:
        scheme, separator, key = authorization.partition(" ")
        if alternate is not None or scheme.lower() != "bearer" or not separator or key != key.strip():
            raise AppError("Provide one valid upload authentication header.", 401)
    else:
        key = alternate or ""
    if not upload_authorized(key):
        raise AppError("A valid Authorization: Bearer or X-Upload-Key header is required.", 401)
    return key


def require_panel_upload_credentials():
    if not current_user.is_authenticated:
        raise AppError("Sign in before uploading.", 401)
    expected = session.get("_csrf_token", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    if (not isinstance(expected, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", supplied)
            or not expected or not secrets.compare_digest(expected.encode(), supplied.encode())):
        raise AppError("A valid X-CSRF-Token header is required before uploading.", 403)


def authorize_upload_request():
    if request.endpoint == "panel.file_upload":
        require_panel_upload_credentials()
        g.upload_principal = "admin"
    elif request.endpoint == "files.upload_file":
        g.upload_principal = "key:" + hash_token(require_upload_key())
    else:
        raise AppError("This endpoint does not accept uploads.", 401)
    g.upload_authorized = True


def ensure_upload_authorized():
    if request.endpoint not in UPLOAD_ENDPOINTS or not g.get("upload_authorized") or not g.get("upload_principal"):
        raise AppError("Upload authorization is required before reading file data.", 401)


def reserve_upload_attempt():
    ensure_upload_authorized()
    now = time.time()
    ip = "ip:" + hash_token(request.remote_addr or "unknown")
    principal = g.upload_principal
    limits = [("global", 20, 60), (ip, 5, 60), (principal, 5, 60), (principal, 60, 3600)]
    with transaction("controls.sqlite3") as connection:
        connection.execute("DELETE FROM upload_events WHERE occurred <= ?", (now - 3600,))
        for key, maximum, seconds in limits:
            row = connection.execute(
                "SELECT count(*), min(occurred) FROM upload_events WHERE key = ? AND occurred > ?",
                (key, now - seconds),
            ).fetchone()
            if row[0] >= maximum:
                wait = max(1, math.ceil(row[1] + seconds - now))
                raise AppError("Upload limit reached. Please wait before retrying.", 429, retry_after=wait)
        connection.executemany("INSERT INTO upload_events (key, occurred) VALUES (?, ?)",
                               [(key, now) for key in ("global", ip, principal)])

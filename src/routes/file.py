import io
import os

from PIL import Image

from core.config import authkey, url_path, use_b2_storage

from flask import send_file, request
from werkzeug.utils import secure_filename

from core.stats import count_hit
from core import actions
from core.b2connect import b2_cache_file
from core.cache import cache_filepath
from core.state import is_deleted


inline_media_mimetypes = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/vnd.microsoft.icon",
    ".avif": "image/avif",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
}


def secure_file_response(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    sandbox = "sandbox allow-same-origin" if response.mimetype.startswith("video/") else "sandbox"
    response.headers["Content-Security-Policy"] = (
        f"{sandbox}; default-src 'none'; media-src 'self'; "
        "script-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    )
    return response


def send_uploaded_file(filepath):
    extension = os.path.splitext(filepath)[1].lower()
    mimetype = inline_media_mimetypes.get(extension)
    response = send_file(
        filepath,
        mimetype=mimetype or "application/octet-stream",
        as_attachment=mimetype is None,
    )
    return secure_file_response(response)


def upload_file():
    key = request.form.get('key', None)
    if not key or key != authkey:
        return "Invalid upload key."

    if 'fileupload' not in request.files:
        return "No file provided."

    uploaded_file = request.files['fileupload']
    success, ret = actions.upload_file(uploaded_file)

    if success:
        return f"{url_path}{ret}"
    else:
        return ret, 500


def get_file(filename):
    filename = secure_filename(filename).replace("/", "")
    try:
        filepath = cache_filepath(filename)
    except ValueError:
        return "Invalid filename.", 400
    if is_deleted(filename):
        return "File does not exist.", 404

    # Check local cache
    if os.path.isfile(filepath):
        count_hit(os.path.basename(filepath))
        try:
            return send_uploaded_file(filepath)
        except FileNotFoundError:
            pass

    if use_b2_storage:
        # If not in cache, check B2
        filepath = b2_cache_file(filename)
        if filepath:
            count_hit(os.path.basename(filepath))
            return send_uploaded_file(filepath)

    return "File does not exist.", 404


def get_file_thumbnail(filename):
    filename = secure_filename(filename).replace("/", "")
    try:
        filepath = cache_filepath(filename)
    except ValueError:
        return "Invalid filename.", 400
    if is_deleted(filename):
        return "File does not exist.", 404

    # ONLY check local cache
    if os.path.isfile(filepath):
        try:
            result = io.BytesIO()
            with Image.open(filepath) as img:
                img.thumbnail((192, 192))
                img.save(result, format="PNG")
            result.seek(0)
            return secure_file_response(send_file(result, mimetype="image/png"))
        except Exception:
            return send_uploaded_file(filepath)

    return "File does not exist.", 404

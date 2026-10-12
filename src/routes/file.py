import io
import os

from PIL import Image

from core.config import authkey, cache_folder, url_path, use_b2_storage

from flask import send_file, request
from werkzeug.utils import secure_filename

from core.stats import count_hit
from core import actions
from core.b2connect import b2_cache_file


inline_image_mimetypes = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/vnd.microsoft.icon",
    ".avif": "image/avif",
}


def secure_file_response(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return response


def send_uploaded_file(filepath):
    extension = os.path.splitext(filepath)[1].lower()
    mimetype = inline_image_mimetypes.get(extension)
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
    filepath = os.path.join(cache_folder, filename)

    # Check local cache
    if os.path.isfile(filepath):
        count_hit(os.path.basename(filepath))
        return send_uploaded_file(filepath)

    if use_b2_storage:
        # If not in cache, check B2
        filepath = b2_cache_file(filename)
        if filepath:
            count_hit(os.path.basename(filepath))
            return send_uploaded_file(filepath)

    return "File does not exist.", 404


def get_file_thumbnail(filename):
    filename = secure_filename(filename).replace("/", "")
    filepath = os.path.join(cache_folder, filename)

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

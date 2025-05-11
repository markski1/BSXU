import io
import os

from PIL import Image

from core.config import authkey, cache_folder, url_path, use_b2_storage

from flask import send_file, request
from werkzeug.utils import secure_filename

from core.stats import count_hit
from core import actions
from core.b2connect import b2_cache_file


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
        return send_file(filepath)

    if use_b2_storage:
        # If not in cache, check B2
        filepath = b2_cache_file(filename)
        if filepath:
            count_hit(os.path.basename(filepath))
            return send_file(filepath)

    return "File does not exist.", 404


def get_file_thumbnail(filename):
    filename = secure_filename(filename).replace("/", "")
    filepath = os.path.join(cache_folder, filename)

    # ONLY check local cache
    if os.path.isfile(filepath):
        print("OK!")
        try:
            # If an image, attempt to indeed make a thumbnail.
            img = Image.open(filepath)
            img.thumbnail((192, 192))
            result = io.BytesIO()
            img.save(result, format=img.format)
            result.seek(0)
            return send_file(result, download_name=filename)
        except Exception as e:
            print(e)
            return send_file(filepath)

    return "File does not exist.", 404

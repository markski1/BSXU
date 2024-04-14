import os

from core.config import authkey, name_length, cache_folder, url_path, app

from flask import send_file, request
from werkzeug.utils import secure_filename

from core.stats import count_hit
from misc import generate_random_string
from core.b2connect import b2_file_upload, b2_cache_file


@app.post("/upload")
def upload_file():
    key = request.form.get('key', None)
    if not key or key != authkey:
        return "Invalid upload key."

    if 'fileupload' not in request.files:
        return "No file provided."

    file = request.files['fileupload']
    filename = secure_filename(file.filename)

    extension = os.path.splitext(filename)[1]
    if extension:
        filename = f"{generate_random_string(name_length)}.{extension}"
    else:
        return "Filename must contain extension."

    filepath = os.path.join(cache_folder, filename)

    # Save to immediate cache
    file.save(filepath)

    # Upload file to B2
    success = b2_file_upload(filepath)

    if not success:
        return "Error uploading file. Check console output for details."

    return f"{url_path}{filename}"


@app.route("/<string:filename>")
def get_file(filename):
    # Check local cache
    filepath = str(os.path.join(cache_folder, filename))
    if os.path.isfile(filepath):
        count_hit(os.path.basename(filepath))
        return send_file(filepath)

    # If not in cache, check B2
    filepath = b2_cache_file(filename)
    if filepath:
        count_hit(os.path.basename(filepath))
        return send_file(filepath)

    return "File does not exist."

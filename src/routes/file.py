import os

from core.config import authkey, name_length, cache_folder, url_path, app, use_b2_storage

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

    uploaded_file = request.files['fileupload']
    filename = secure_filename(uploaded_file.filename)
    filename = filename.replace("/", "")

    extension = os.path.splitext(filename)[1]
    if extension:
        filename = f"{generate_random_string(name_length)}{extension}"
    else:
        return "Filename must contain extension."

    filepath = os.path.join(cache_folder, filename)

    # Save to immediate cache
    try:
        uploaded_file.save(filepath)
    except Exception as e:
        print(f"Error saving file to cache: {e}")
        return "Error caching file. Check console output for details."

    if use_b2_storage:
        # Upload file to B2
        success = b2_file_upload(filepath)

        if not success:
            return "Error uploading file. Check console output for details."

    return f"{url_path}{filename}"


@app.route("/<string:filename>")
def get_file(filename):
    # Sanitize
    filename = secure_filename(filename)
    filename = filename.replace("/", "")
    # Cache path
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

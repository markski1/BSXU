import os

from core.config import authkey, cache_folder, url_path, app, use_b2_storage

from flask import send_file, request
from werkzeug.utils import secure_filename

from core.stats import count_hit
from core import actions
from core.b2connect import b2_cache_file


@app.post("/upload")
def upload_file():
    key = request.form.get('key', None)
    if not key or key != authkey:
        return "Invalid upload key."

    if 'fileupload' not in request.files:
        return "No file provided."

    uploaded_file = request.files['fileupload']

    success, ret = actions.upload_file(uploaded_file)

    # if succeeded, ret is a filename.
    # if failed, ret is an error message.
    if success:
        return f"{url_path}{ret}"
    else:
        return ret, 500


@app.route("/<string:filename>")
def get_file(filename):
    # Sanitize
    filename = secure_filename(filename).replace("/", "")

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


# ONLY checks cache, DOES NOT count hit
@app.route("/cache/<string:filename>")
def get_cache_file(filename):
    # Sanitize
    filename = secure_filename(filename).replace("/", "")

    # Cache path
    filepath = os.path.join(cache_folder, filename)

    # Check local cache
    if os.path.isfile(filepath):
        return send_file(filepath)

    return "File does not exist.", 404

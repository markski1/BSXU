import os
from flask import Flask, send_file, request
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

from misc import generate_random_string
from b2connect import b2_file_upload, b2_file_exists, b2_cache_file

load_dotenv()

authkey = os.getenv('AUTH_KEY')
url_path = os.getenv('URL_PATH')
name_length = os.getenv('NAME_LENGTH')
cache_folder = os.getenv('CACHE_FOLDER')
app = Flask(__name__)


@app.route("/")
def index():
    return 'ok'


@app.post("/upload")
def upload_file():
    key = request.form.get('key', None)
    if not key or key != authkey:
        return "Invalid upload key."

    if 'fileupload' not in request.files:
        return "No file provided."

    file = request.files['fileupload']
    filename = secure_filename(file.filename)

    if '.' in filename:
        filename = f"{generate_random_string(name_length)}.{filename.split('.')[1]}"
    else:
        return "Filename does not contain extension."

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
    filepath = os.path.join(cache_folder, filename)
    if os.path.isfile(filepath):
        return send_file(filepath)

    # If not in cache, check B2
    if b2_file_exists(filename):
        filepath = b2_cache_file(filename)
        if not filepath:
            return "Error caching file. Check console output for details."
        return send_file(filepath)

    return "File does not exist."


if __name__ == "__main__":
    app.run(host=os.getenv('APP_HOST'), port=os.getenv('APP_PORT'), debug=os.getenv('APP_DEBUG'))

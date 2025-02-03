import os

from core.config import name_length, cache_folder, use_b2_storage
from core.b2connect import b2_file_upload
from werkzeug.utils import secure_filename
from misc import generate_random_string, wh_report


def upload_file(uploaded_file, custom_file_name=None):
    filename = secure_filename(uploaded_file.filename)
    filename = filename.replace("/", "")

    extension = os.path.splitext(filename)[1]
    if extension:
        if custom_file_name:
            filename = custom_file_name + extension
        else:
            filename = generate_random_string(name_length) + extension
    else:
        return False, "Filename must contain extension."

    filepath = os.path.join(cache_folder, filename)

    # Save to immediate cache
    try:
        uploaded_file.save(filepath)
    except Exception as e:
        wh_report(f"Error saving file to cache.", e)
        return False, "Error uploading file to server. Check console output for details."

    if use_b2_storage:
        # Upload file to B2
        success = b2_file_upload(filepath)

        if not success:
            return False, "Error uploading file to B2. Check console output for details."

    return True, filename

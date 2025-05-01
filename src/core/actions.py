import os
from typing import Optional

from werkzeug.datastructures import FileStorage

from core.config import name_length, cache_folder, use_b2_storage
from core.b2connect import b2_file_upload
from werkzeug.utils import secure_filename
from misc import generate_random_string, wh_report


def upload_file(uploaded_file: FileStorage, custom_file_name: Optional[str] = None) -> tuple[bool, str]:
    """
    Upload a file to the default Backblaze B2 bucket.
    :param uploaded_file: A 'FileStorage' object provided by Flask.
    :param custom_file_name: Optionally, a custom name for the file.
    :return: A boolean indicating success and a string. If successful, the string is a filename,
        otherwise the string is an error message.
    """
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
        # Upload the file to B2
        success = b2_file_upload(filepath)

        if not success:
            return False, "Error uploading file to B2. Check console output for details."

    return True, filename

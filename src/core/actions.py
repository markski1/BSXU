import io
import os
from typing import Optional

from PIL import Image
from werkzeug.datastructures import FileStorage

from core.cache import cache_filepath, has_cache_capacity, make_cache_room, staged_file
from core.config import (
    max_upload_size_mb,
    name_length,
    use_b2_storage,
)
from core.b2connect import b2_file_exists, b2_file_upload
from core.state import file_lock, storage_transaction
from werkzeug.utils import secure_filename
from misc import generate_random_string, wh_report


def prune_metadata(working_file: FileStorage) -> FileStorage:
    """
    Provided a FileStorage, if it is an image, return it without metadata.
    :param working_file: A 'FileStorage' object provided by Flask.
    :return: A 'FileStorage' object.
    """
    try:
        # Not pretty, but the most reliable way to decide if something is an image
        # really does seem to be giving it to Pillow and seeing if it blows up.
        image = Image.open(working_file)
        output = io.BytesIO()
        image.save(output, format=image.format)
        output.seek(0)

        # We got a werkzeug FileStorage, so we return one of those too.
        cleaned_file = FileStorage(
            stream=output,
            filename=working_file.filename,
            content_type=working_file.content_type,
            content_length=output.getbuffer().nbytes
        )
        return cleaned_file
    except Exception:
        # If not an image or error occurs, return the original file
        working_file.seek(0)
        return working_file


def reserve_filename(filename, file_size):
    filepath = cache_filepath(filename)
    with storage_transaction() as connection:
        reserved = connection.execute(
            "SELECT 1 FROM uploaded_names WHERE filename = ?", (filename,)
        ).fetchone()
        if reserved or os.path.lexists(filepath):
            return None
        if not has_cache_capacity(file_size, connection, use_b2_storage):
            raise ValueError("File would exceed the configured cache size limit.")
        connection.execute(
            "INSERT INTO uploaded_names (filename, backed_up, deleted) VALUES (?, 0, 1)",
            (filename,),
        )
    return filepath


def upload_file(uploaded_file: FileStorage, custom_file_name: Optional[str] = None) -> tuple[bool, str]:
    """
    Upload a file to the default Backblaze B2 bucket.
    :param uploaded_file: A 'FileStorage' object provided by Flask.
    :param custom_file_name: Optionally, a custom name for the file.
    :return: A boolean indicating success and a string. If successful, the string is a filename,
        otherwise the string is an error message.
    """
    filename = secure_filename(uploaded_file.filename or "")
    extension = os.path.splitext(filename)[1]
    if not extension:
        return False, "Filename must contain extension."
    custom_name = secure_filename(custom_file_name) if custom_file_name else None
    if custom_file_name and not custom_name:
        return False, "Invalid custom filename."

    # In case of being an image, try and prune metadata.
    clean_file = prune_metadata(uploaded_file)

    clean_file.stream.seek(0, os.SEEK_END)
    file_size = clean_file.stream.tell()
    clean_file.stream.seek(0)

    if file_size > max_upload_size_mb * 1024 * 1024:
        if clean_file is not uploaded_file:
            clean_file.close()
        return False, f"File exceeds the {max_upload_size_mb} MB upload limit."

    try:
        with staged_file() as temporary_path:
            clean_file.save(temporary_path)
            for _ in range(10):
                filename = (custom_name or generate_random_string(name_length)) + extension
                with file_lock(filename):
                    filepath = reserve_filename(filename, file_size)
                    if filepath is not None and use_b2_storage:
                        try:
                            exists_in_b2 = b2_file_exists(filename)
                        except Exception:
                            with storage_transaction() as connection:
                                connection.execute(
                                    "DELETE FROM uploaded_names WHERE filename = ?", (filename,)
                                )
                            raise
                        if exists_in_b2:
                            with storage_transaction() as connection:
                                connection.execute(
                                    "UPDATE uploaded_names SET backed_up = 1, deleted = 0 "
                                    "WHERE filename = ?", (filename,),
                                )
                            filepath = None
                    if filepath is None:
                        if custom_name:
                            return False, "Filename already exists or was previously used."
                        continue

                    if use_b2_storage and not b2_file_upload(str(temporary_path), filename):
                        return False, "Error uploading file to B2. Check console output for details."
                    with storage_transaction() as connection:
                        make_cache_room(file_size, connection, use_b2_storage)
                        os.replace(temporary_path, filepath)
                        connection.execute(
                            "UPDATE uploaded_names SET backed_up = ?, deleted = 0 WHERE filename = ?",
                            (int(use_b2_storage), filename),
                        )
                    return True, filename
        return False, "Unable to generate an unused filename."
    except ValueError as exception:
        return False, str(exception)
    except Exception as exception:
        wh_report("Error uploading file.", exception)
        return False, "Error uploading file to server. Check console output for details."
    finally:
        if clean_file is not uploaded_file:
            clean_file.close()

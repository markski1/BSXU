from contextlib import contextmanager
import os
from pathlib import Path
import tempfile

from core.config import cache_folder, cache_max_size_mb, use_b2_storage
from core.state import storage_transaction
from misc import wh_report


def cache_filepath(filename: str) -> Path:
    if (not filename or filename in {".", ".."}
            or any(character in filename for character in ("/", "\\", ":", "\0"))
            or filename != filename.rstrip(" .")):
        raise ValueError("Invalid filename.")
    root = Path(cache_folder).resolve()
    target = root / filename
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError("Filename must stay inside the cache.")
    return target


def get_size(filepath: str) -> int:
    total_size = 0
    for dirpath, directories, filenames in os.walk(filepath):
        directories[:] = [name for name in directories if name != ".staging"]
        for filename in filenames:
            target = Path(dirpath) / filename
            if not target.is_symlink():
                try:
                    total_size += target.stat().st_size
                except FileNotFoundError:
                    pass
    return total_size


@contextmanager
def staged_file():
    root = Path(cache_folder).resolve()
    staging = root / ".staging"
    if staging.is_symlink():
        raise ValueError("The staging directory must not be a symbolic link.")
    staging.mkdir(exist_ok=True)
    for leftover in staging.glob("*.part"):
        if leftover.is_file() and not leftover.is_symlink():
            leftover.unlink()
    descriptor, filename = tempfile.mkstemp(dir=staging, suffix=".part")
    os.close(descriptor)
    target = Path(filename)
    try:
        yield target
    finally:
        target.unlink(missing_ok=True)


def eviction_candidates(connection):
    candidates = []
    for (filename,) in connection.execute(
        "SELECT filename FROM uploaded_names WHERE backed_up = 1 AND deleted = 0"
    ):
        target = cache_filepath(filename)
        if target.is_file():
            candidates.append(target)
    return sorted(candidates, key=lambda target: target.stat().st_mtime)


def has_cache_capacity(file_size, connection, allow_eviction):
    capacity = cache_max_size_mb * 1024 * 1024
    if file_size > capacity:
        return False
    available = capacity - get_size(cache_folder)
    if allow_eviction:
        available += sum(target.stat().st_size for target in eviction_candidates(connection))
    return file_size <= available


def make_cache_room(file_size, connection, allow_eviction):
    if not has_cache_capacity(file_size, connection, allow_eviction):
        raise ValueError("File would exceed the configured cache size limit.")
    capacity = cache_max_size_mb * 1024 * 1024
    size = get_size(cache_folder)
    if size + file_size <= capacity:
        return
    for target in eviction_candidates(connection):
        target_size = target.stat().st_size
        target.unlink()
        size -= target_size
        if size + file_size <= capacity:
            return


def clear_cache(path: str) -> None:
    root = Path(cache_folder).resolve()
    if Path(path).resolve() != root:
        raise ValueError("Only the configured cache can be cleared.")
    with storage_transaction() as connection:
        for target in root.iterdir():
            if target.is_dir() and not target.is_symlink():
                continue
            try:
                target.unlink()
                if not use_b2_storage:
                    connection.execute(
                        "UPDATE uploaded_names SET deleted = 1 WHERE filename = ?",
                        (target.name,),
                    )
            except OSError as exception:
                wh_report("Error clearing a cached file.", exception)
    wh_report("Cache has been cleared.")


def cache_delete_file(filename: str) -> bool:
    target = cache_filepath(filename)
    with storage_transaction() as connection:
        try:
            target.unlink()
            if not use_b2_storage:
                connection.execute(
                    "UPDATE uploaded_names SET deleted = 1 WHERE filename = ?", (filename,)
                )
            return True
        except FileNotFoundError:
            return False
        except OSError as exception:
            wh_report("Error deleting a cached file.", exception)
            return False

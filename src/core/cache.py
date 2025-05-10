import os
import shutil

from core.config import cache_folder
from misc import wh_report


def get_size(filepath: str) -> int:
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(filepath):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            # skip if symbolic link
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)

    return total_size


def clear_cache(path: str) -> None:
    files = [os.path.join(cache_folder, filename) for filename in os.listdir(path)]
    for filename in files:
        try:
            shutil.rmtree(filename)
        except Exception as e:
            wh_report(f"Exception found when attempting to clear the cache. File being deleted: {filename}", e)
            break

    wh_report("Cache has been cleared.")


def cache_delete_file(filename) -> None:
    cache_filename = os.path.join(cache_folder, filename)

    try:
        os.remove(cache_filename)
    except Exception as e:
        wh_report(f"Exception found when attempting to delete a file from the cache. "
                  f"File being deleted: {cache_filename}", e)

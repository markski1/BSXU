from contextlib import closing, contextmanager
import errno
import hashlib
import os
from pathlib import Path
import sqlite3
import time

if os.name == "nt":
    import msvcrt
else:
    import fcntl


state_folder = None


def connect(database):
    return sqlite3.connect(state_folder / database, timeout=30)


def init_app(app):
    global state_folder
    state_folder = Path(app.config["STATE_FOLDER"]).resolve()
    cache_path = Path(app.config["CACHE_FOLDER"]).resolve()
    if state_folder.is_relative_to(cache_path):
        raise RuntimeError("STATE_FOLDER must be outside CACHE_FOLDER.")
    state_folder.mkdir(parents=True, exist_ok=True)
    (state_folder / "locks").mkdir(exist_ok=True)
    with closing(connect("storage.sqlite3")) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS uploaded_names ("
            "filename TEXT PRIMARY KEY COLLATE NOCASE, backed_up INTEGER NOT NULL, "
            "deleted INTEGER NOT NULL DEFAULT 0)"
        )
        connection.commit()
    with closing(connect("sessions.sqlite3")) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS active_session ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), token_hash TEXT NOT NULL)"
        )
        connection.commit()


@contextmanager
def file_lock(filename):
    lock_name = hashlib.sha256(filename.casefold().encode()).hexdigest() + ".lock"
    with (state_folder / "locks" / lock_name).open("a+b") as stream:
        if os.name == "nt":
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exception:
                    if exception.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    time.sleep(0.05)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def storage_transaction():
    with closing(connect("storage.sqlite3")) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            yield connection


def is_deleted(filename):
    with closing(connect("storage.sqlite3")) as connection:
        result = connection.execute(
            "SELECT deleted FROM uploaded_names WHERE filename = ?", (filename,)
        ).fetchone()
    return bool(result and result[0])

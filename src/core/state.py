from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


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

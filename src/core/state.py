from contextlib import closing, contextmanager
import errno
import hashlib
import os
import sqlite3
import time

from core.config import settings
from core.errors import AppError

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def connect(database="storage.sqlite3"):
    connection = sqlite3.connect(settings().state_folder / database, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def transaction(database="storage.sqlite3"):
    with closing(connect(database)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            yield connection


storage_transaction = transaction


def init_app(app):
    config = settings()
    config.state_folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    (config.state_folder / "locks-v2").mkdir(exist_ok=True, mode=0o700)
    with closing(connect()) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS uploaded_names (filename TEXT PRIMARY KEY COLLATE NOCASE, "
                "backed_up INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(uploaded_names)")}
            additions = {
                "status": "TEXT NOT NULL DEFAULT 'available'", "size": "INTEGER NOT NULL DEFAULT 0",
                "cached": "INTEGER NOT NULL DEFAULT 0", "original_name": "TEXT NOT NULL DEFAULT ''",
                "created": "REAL NOT NULL DEFAULT 0", "accessed": "REAL NOT NULL DEFAULT 0",
                "sha256": "TEXT NOT NULL DEFAULT ''", "hits": "INTEGER NOT NULL DEFAULT 0",
                "expires": "REAL", "revoked": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE uploaded_names ADD COLUMN {name} {definition}")
            connection.execute("UPDATE uploaded_names SET status = 'deleted' WHERE deleted = 1")
            connection.execute("CREATE INDEX IF NOT EXISTS file_listing ON uploaded_names(status, created)")
            connection.execute("CREATE INDEX IF NOT EXISTS cache_eviction ON uploaded_names(cached, backed_up, accessed)")
            connection.execute("CREATE TABLE IF NOT EXISTS staging (id TEXT PRIMARY KEY, bytes INTEGER NOT NULL, expires REAL NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS thumbnails (filename TEXT PRIMARY KEY, size INTEGER NOT NULL, created REAL NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires REAL NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS upload_keys (id TEXT PRIMARY KEY, label TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, created REAL NOT NULL, last_used REAL)")
    with closing(connect("controls.sqlite3")) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS misses (key TEXT PRIMARY KEY, expires REAL NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS leases (id TEXT PRIMARY KEY, kind TEXT NOT NULL, expires REAL NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS upload_events (id INTEGER PRIMARY KEY, key TEXT NOT NULL, occurred REAL NOT NULL)")
            connection.execute("CREATE INDEX IF NOT EXISTS upload_event_window ON upload_events(key, occurred)")


@contextmanager
def file_lock(filename, timeout=10):
    stripe = int.from_bytes(hashlib.sha256(filename.casefold().encode()).digest()[:2], "big") % 64
    target = settings().state_folder / "locks-v2" / f"{stripe:02d}.lock"
    with target.open("a+b") as stream:
        if os.name == "nt":
            stream.seek(0, os.SEEK_END)
            if not stream.tell():
                stream.write(b"\0")
                stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exception:
                if exception.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise AppError("This file is busy. Please retry.", 503) from exception
                time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def file_record(filename):
    with closing(connect()) as connection:
        return connection.execute("SELECT * FROM uploaded_names WHERE filename = ?", (filename,)).fetchone()


def is_deleted(filename):
    row = file_record(filename)
    return bool(row and (row["status"] != "available" or row["revoked"] or (row["expires"] and row["expires"] <= time.time())))

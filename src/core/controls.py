from contextlib import closing, contextmanager
import hashlib
import math
import secrets
import threading
import time

from flask import current_app

from core.errors import AppError
from core.state import connect, transaction


def rate_limit(scope, identifier, limit, seconds=60):
    now = time.time()
    key = hashlib.sha256(f"{scope}:{identifier}:{int(now // seconds)}".encode()).hexdigest()
    if scope.endswith("-global"):
        key = f"global:{scope}:{int(now // seconds)}"
    with transaction("controls.sqlite3") as connection:
        connection.execute("DELETE FROM limits WHERE expires <= ?", (now,))
        count = connection.execute("SELECT count FROM limits WHERE key = ?", (key,)).fetchone()
        if count and count[0] >= limit:
            retry_after = max(1, math.ceil((int(now // seconds) + 1) * seconds - now))
            raise AppError("Too many requests. Please retry later.", 429, retry_after=retry_after)
        connection.execute(
            "INSERT INTO limits VALUES (?, 1, ?) ON CONFLICT(key) DO UPDATE SET count = count + 1",
            (key, (int(now // seconds) + 1) * seconds),
        )
        connection.execute("DELETE FROM limits WHERE key IN (SELECT key FROM limits WHERE key NOT LIKE 'global:%' ORDER BY expires DESC LIMIT -1 OFFSET 4096)")


def missing(key):
    digest = hashlib.sha256(key.encode()).hexdigest()
    with closing(connect("controls.sqlite3")) as connection:
        row = connection.execute("SELECT expires FROM misses WHERE key = ?", (digest,)).fetchone()
    return bool(row and row[0] > time.time())


def mark_missing(key):
    with transaction("controls.sqlite3") as connection:
        connection.execute("DELETE FROM misses WHERE expires <= ?", (time.time(),))
        connection.execute("INSERT OR REPLACE INTO misses VALUES (?, ?)", (hashlib.sha256(key.encode()).hexdigest(), time.time() + 60))
        connection.execute("DELETE FROM misses WHERE key IN (SELECT key FROM misses ORDER BY expires DESC LIMIT -1 OFFSET 4096)")


def forget_missing(key):
    with transaction("controls.sqlite3") as connection:
        connection.execute("DELETE FROM misses WHERE key = ?", (hashlib.sha256(key.encode()).hexdigest(),))


@contextmanager
def lease(kind, limit, duration=300):
    token = secrets.token_hex(16)
    with transaction("controls.sqlite3") as connection:
        connection.execute("DELETE FROM leases WHERE expires <= ?", (time.time(),))
        count = connection.execute("SELECT count(*) FROM leases WHERE kind = ?", (kind,)).fetchone()[0]
        if count >= limit:
            raise AppError("The server is busy. Please retry shortly.", 503)
        connection.execute("INSERT INTO leases VALUES (?, ?, ?)", (token, kind, time.time() + duration))
    app = current_app._get_current_object()
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(30):
            try:
                with app.app_context():
                    renew_lease(token)
            except Exception:
                app.logger.exception("Could not renew an active resource lease.")

    worker = threading.Thread(target=heartbeat, daemon=True, name="bsxu-lease")
    try:
        worker.start()
        yield token
    finally:
        stopped.set()
        if worker.ident is not None:
            worker.join(timeout=1)
        with transaction("controls.sqlite3") as connection:
            connection.execute("DELETE FROM leases WHERE id = ?", (token,))


def renew_lease(token):
    with transaction("controls.sqlite3") as connection:
        connection.execute("UPDATE leases SET expires = ? WHERE id = ?", (time.time() + 300, token))

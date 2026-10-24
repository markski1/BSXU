from contextlib import closing
import hmac
import secrets
import time

from flask_login import LoginManager, UserMixin

from core.config import settings
from core.errors import AppError
from core.state import connect, transaction


class Session(UserMixin):
    def __init__(self, token):
        self.id = token


def hash_token(token):
    return hmac.new(settings().secret_key.encode(), token.encode(), "sha256").hexdigest()


def init_app(app):
    manager = LoginManager()
    manager.login_view = "panel.login_prompt"
    manager.session_protection = "strong"
    manager.init_app(app)

    @manager.user_loader
    def load_user(token):
        if not isinstance(token, str) or len(token) > 128:
            return None
        with closing(connect()) as connection:
            row = connection.execute("SELECT expires FROM sessions WHERE token_hash = ?", (hash_token(token),)).fetchone()
        return Session(token) if row and row["expires"] > time.time() else None


def attempt_login(key):
    if not secrets.compare_digest(key.encode(), settings().admin_key.encode()):
        return None
    token = secrets.token_urlsafe(32)
    with transaction() as connection:
        connection.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
        connection.execute("INSERT INTO sessions VALUES (?, ?)", (hash_token(token), time.time() + 8 * 3600))
        connection.execute("DELETE FROM sessions WHERE token_hash IN (SELECT token_hash FROM sessions ORDER BY expires DESC LIMIT -1 OFFSET 20)")
    return Session(token)


def revoke_session(token):
    with transaction() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))


def upload_authorized(key):
    if not key or len(key) > 256:
        return False
    digest = hash_token(key)
    with transaction() as connection:
        row = connection.execute("SELECT id FROM upload_keys WHERE token_hash = ?", (digest,)).fetchone()
        if row:
            connection.execute("UPDATE upload_keys SET last_used = ? WHERE id = ?", (time.time(), row["id"]))
    return bool(row)


def create_upload_key(label):
    label = label.strip()
    if not label or len(label) > 80:
        raise AppError("Give the upload key a name between 1 and 80 characters.")
    token = secrets.token_urlsafe(32)
    with transaction() as connection:
        count = connection.execute("SELECT count(*) FROM upload_keys").fetchone()[0]
        if count >= 50:
            raise AppError("Revoke an unused upload key before adding another.")
        connection.execute("INSERT INTO upload_keys (id, label, token_hash, created) VALUES (?, ?, ?, ?)",
                           (secrets.token_hex(8), label, hash_token(token), time.time()))
    return token

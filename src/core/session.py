from typing import Optional
from contextlib import closing
import hmac
import secrets

from flask_login import (
    LoginManager,
    UserMixin
)

from misc import generate_random_string
from core import state

import os
from dotenv import load_dotenv

load_dotenv()

authkey = os.getenv('AUTH_KEY')


def init_app(app):
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.session_protection = "strong"

    @login_manager.user_loader
    def user_loader(loaded_token: str) -> Optional[Session]:
        token_hash = hash_session_token(loaded_token)
        with closing(state.connect("sessions.sqlite3")) as connection:
            result = connection.execute(
                "SELECT token_hash FROM active_session WHERE id = 1"
            ).fetchone()
        if result and secrets.compare_digest(result[0], token_hash):
            user_model = Session()
            user_model.id = loaded_token
            return user_model
        return None


def attempt_login(auth_key: str) -> Optional[str]:
    if authkey and secrets.compare_digest(auth_key.encode(), authkey.encode()):
        token = generate_random_string(32)
        token_hash = hash_session_token(token)
        with closing(state.connect("sessions.sqlite3")) as connection:
            with connection:
                connection.execute(
                    "INSERT OR REPLACE INTO active_session (id, token_hash) VALUES (1, ?)",
                    (token_hash,),
                )
        return token

    return None


def hash_session_token(token: str) -> str:
    return hmac.new((authkey or "").encode(), token.encode(), "sha256").hexdigest()


def revoke_session(token: str) -> None:
    token_hash = hash_session_token(token)
    with closing(state.connect("sessions.sqlite3")) as connection:
        with connection:
            connection.execute(
                "DELETE FROM active_session WHERE token_hash = ?", (token_hash,)
            )


class Session(UserMixin):
    pass

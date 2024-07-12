from flask_login import (
    LoginManager,
    UserMixin
)

from misc import generate_random_string

import os
from dotenv import load_dotenv

load_dotenv()

authkey = os.getenv('AUTH_KEY')
gen_token = ""


def init_app(app):
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.session_protection = "strong"

    @login_manager.user_loader
    def user_loader(load_id):
        if load_id == gen_token:
            user_model = Session()
            user_model.id = load_id
            return user_model

    return None


def attempt_login(auth_key):
    global gen_token
    if auth_key == authkey:
        gen_token = generate_random_string(32)
        return gen_token

    return None


class Session(UserMixin):
    ...

from flask import Blueprint, render_template, request, redirect
from flask_login import login_required, login_user, logout_user, current_user

from core.session import attempt_login, Session

panel_bp = Blueprint("panel", __name__, url_prefix="/panel")


@panel_bp.route("/")
def login_prompt():
    if current_user.is_authenticated:
        return redirect("/panel/main")
    else:
        return render_template("login.html")


@panel_bp.post("/login")
def login():
    auth_key = request.form.get("auth_key", "")

    result = attempt_login(auth_key)

    if result is not None:
        user_model = Session()
        user_model.id = result
        login_user(user_model)
        return redirect("/panel/")
    else:
        return "Invalid auth key."


@panel_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("../")


@panel_bp.route("/main")
@login_required
def main_ui():
    return render_template("panel.html")


@panel_bp.route("/cached")
@login_required
def cache_files_ui():
    return render_template("cache.html")


@panel_bp.route("/all-files")
@login_required
def files_ui():
    return render_template("files.html")

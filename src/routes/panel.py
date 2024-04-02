import os
import shutil

from flask import Blueprint, render_template, request, redirect
from flask_login import login_required, login_user, logout_user, current_user

from core.session import attempt_login, Session
from core.config import cache_folder

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
    file_list, total_size = cached_file_data()
    return render_template("panel.html",
                           cache_amount=len(file_list), cache_size=f'{total_size:,}')


@panel_bp.route("/cache")
@login_required
def cache_files_ui():
    file_list, total_size = cached_file_data()
    return render_template("cache.html", cached_files=file_list, total_size=f'{total_size:,}')


@panel_bp.route("/stats")
@login_required
def stats_ui():
    return "Not yet implemented."


# Actions


@panel_bp.route("/cache/clear")
@login_required
def clear_cache():
    for filename in os.listdir(cache_folder):
        file_path = os.path.join(cache_folder, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            return f'Failed to delete {file_path}. Reason: {e}', 500

    return redirect('/panel/cache')


# Helpers


def cached_file_data():
    file_list = []
    total_size = 0
    for fname in os.listdir(cache_folder):
        file_path = os.path.join(cache_folder, fname)
        if os.path.isfile(file_path):
            file_size_kb = os.path.getsize(file_path) / 1024
            file_list.append({'name': fname, 'size': int(file_size_kb)})
            total_size += file_size_kb

    return file_list, int(total_size)

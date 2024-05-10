import os
import shutil

from flask import Blueprint, render_template, request, redirect
from flask_login import login_required, login_user, logout_user, current_user

from core import actions
from core.session import attempt_login, Session
from core.config import cache_folder, use_b2_storage
from core.stats import get_total_hits, start_date, get_all_file_hits

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
                           cache_amount=len(file_list), cache_size=f'{total_size:,}',
                           total_hits=get_total_hits(), start_date=start_date)


@panel_bp.route("/cache")
@login_required
def cache_files_ui():
    file_list, total_size = cached_file_data()
    return render_template("cache.html", cached_files=file_list, total_size=f'{total_size:,}',
                           use_b2_storage=use_b2_storage)


@panel_bp.route("/stats")
@login_required
def stats_ui():
    return render_template("stats.html",
                           start_date=start_date, file_hits=get_all_file_hits())


@panel_bp.route("/upload")
@login_required
def upload_ui():
    return render_template("upload.html")


# Actions


@panel_bp.post("/file-upload")
@login_required
def file_upload():
    if 'fileupload' not in request.files:
        return "No file provided.", 400

    uploaded_file = request.files['fileupload']
    file_name = request.form.get('file-name')

    if file_name is not None and len(file_name) > 0:
        success, ret = actions.upload_file(uploaded_file, file_name)
    else:
        success, ret = actions.upload_file(uploaded_file)

    if success:
        return render_template(
            'result.html',
            result_title="File uploaded",
            result_outcome=f"The file has been uploaded: `{ret}`"
        )
    else:
        return render_template(
            'result.html',
            result_title="Upload failed",
            result_outcome=ret
        )


@panel_bp.route("/cache/clear/<string:confirmation>")
@login_required
def clear_cache(confirmation):
    if not use_b2_storage and "override" not in confirmation:
        return """
                    <p>Be advised: You do NOT have B2 storage enabled. The 'cache' is currently used as your main 
                    and only method of storage.</p>
                    <p>Doing this will delete ALL of your files.</p>
                    <p><a href="/panel/cache/clear/override">Yes, delete all of my files.</a></p>
               """
    else:
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

import os

from flask import Blueprint, render_template, request, redirect
from flask_login import login_required, login_user, logout_user, current_user

from core import actions
from core.b2connect import b2_get_files, b2_delete_file
from core.cache import cache_delete_file
from core.session import attempt_login, Session
from core.config import cache_folder, use_b2_storage, url_path
from core.stats import get_total_hits, start_date, get_all_file_hits

panel_bp = Blueprint("panel", __name__, url_prefix="/panel")


@panel_bp.route("/")
def login_prompt():
    if current_user.is_authenticated:
        return redirect("/panel/main")
    else:
        return render_template("login.jinja2")


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
    return render_template("panel.jinja2", use_b2_storage=use_b2_storage,
                           cache_amount=len(file_list), cache_size=total_size,
                           total_hits=get_total_hits(), start_date=start_date)


@panel_bp.route("/files")
@login_required
def files_ui():
    if not use_b2_storage:
        return render_template(
            'result.jinja2',
            result_title="B2 upload disabled.",
            result_outcome=f"This endpoint is disabled because B2 storage is disabled."
        )

    b2_files = b2_get_files()

    total_size = 0
    for f in b2_files:
        total_size += f.size

    return render_template(
        'b2_files.jinja2', files=b2_files, total_size=total_size
    )


@panel_bp.route("/files/delete/<string:filename>")
@login_required
def delete_file(filename):
    if not use_b2_storage:
        return render_template(
            'result.jinja2',
            result_title="B2 upload disabled.",
            result_outcome=f"This endpoint is disabled because B2 storage is disabled."
        )

    success = b2_delete_file(filename)

    if success:
        cache_delete_file(filename)
        return render_template(
            'result.jinja2',
            result_title="File deleted",
            result_outcome=f"The file `{filename}` has been deleted."
        )
    else:
        return render_template(
            'result.jinja2',
            result_title="File deletion failed",
        )


@panel_bp.route("/cache")
@login_required
def cache_files_ui():
    file_list, total_size = cached_file_data()
    return render_template("cache.jinja2", cached_files=file_list, total_size=f'{total_size:,}',
                           use_b2_storage=use_b2_storage, url_path=url_path)


@panel_bp.route("/cache/delete/<string:filename>")
@login_required
def delete_cache_file(filename):
    cache_delete_file(filename)
    return render_template(
        'result.jinja2',
        result_title="File deleted fron cache",
        result_outcome=f"The file `{filename}` has been deleted from cache."
    )


@panel_bp.route("/stats")
@login_required
def stats_ui():
    return render_template("stats.jinja2",
                           start_date=start_date, file_hits=get_all_file_hits())


@panel_bp.route("/upload")
@login_required
def upload_ui():
    return render_template("upload.jinja2")


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
            'result.jinja2',
            result_title="File uploaded",
            result_outcome=f"The file has been uploaded: `{url_path}{ret}`"
        )
    else:
        return render_template(
            'result.jinja2',
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

    clear_cache()
    return redirect('/panel/cache')


# Helpers

image_extensions = [".jpg", ".jpeg", ".png", ".gif"]


def cached_file_data():
    file_list = []
    total_size = 0
    for fname in os.listdir(cache_folder):
        file_path = os.path.join(cache_folder, fname)
        if os.path.isfile(file_path):
            file_size_kb = os.path.getsize(file_path) / 1024

            file_list.append(
                {
                    'name': fname,
                    'size': int(file_size_kb),
                    'is_image': True if any(ext in fname for ext in image_extensions) else False
                }
            )
            total_size += file_size_kb

    return file_list, int(total_size)

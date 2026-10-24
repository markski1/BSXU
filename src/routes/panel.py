from contextlib import closing
import math
from pathlib import Path
import re
import secrets
import time
from urllib.parse import quote

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from core import actions, b2connect
from core.cache import cache_delete_file, cache_filepath, clear_cache
from core.config import settings
from core.errors import AppError
from core.media import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from core.session import attempt_login, create_upload_key, revoke_session
from core.state import connect, file_record, transaction
from core.uploads import MAX_BATCH_FILES, ensure_upload_authorized, require_panel_upload_credentials, reserve_upload_attempt

panel_bp = Blueprint("panel", __name__, url_prefix="/panel")

FILE_TYPES = {
    "image": ("Image", "jpg jpeg png gif webp avif bmp ico svg tif tiff heic heif jxl psd raw dng cr2 nef"),
    "video": ("Video", "mp4 m4v webm ogv mov mkv avi wmv flv mpg mpeg 3gp mts m2ts"),
    "audio": ("Audio", "mp3 m4a aac wav flac ogg oga opus aiff aif wma mid midi"),
    "pdf": ("PDF document", "pdf"),
    "document": ("Document", "doc docx odt rtf pages epub mobi"),
    "spreadsheet": ("Spreadsheet", "xls xlsx xlsm ods csv tsv numbers"),
    "presentation": ("Presentation", "ppt pptx pps ppsx odp key"),
    "archive": ("Archive", "zip rar 7z tar gz bz2 xz zst tgz tbz2 txz cab"),
    "code": ("Code", "py pyw js mjs cjs jsx ts tsx html htm css scss sass less json xml yaml yml toml ini cfg conf sh bash ps1 bat cmd c h cpp hpp cs java go rs rb php sql ipynb vue svelte"),
    "text": ("Text", "txt md markdown rst log tex srt vtt"),
    "font": ("Font", "ttf otf woff woff2"),
    "application": ("Application / disk image", "exe msi msix appx dmg pkg deb rpm apk appimage iso img"),
}
FILE_TYPES_BY_EXTENSION = {
    "." + extension: (kind, label)
    for kind, (label, extensions) in FILE_TYPES.items()
    for extension in extensions.split()
}


@panel_bp.app_template_filter("file_type")
def file_type(filename):
    return FILE_TYPES_BY_EXTENSION.get(Path(filename).suffix.lower(), ("file", "File"))


def get_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


@panel_bp.context_processor
def panel_context():
    return {"csrf_token": get_csrf_token, "config": settings(), "now": time.time()}


@panel_bp.before_request
def validate_csrf_token():
    if request.method != "POST":
        return
    if request.endpoint == "panel.file_upload":
        ensure_upload_authorized()
        return
    if request.endpoint != "panel.login" and not current_user.is_authenticated:
        abort(401, description="Sign in before making changes.")
    expected = session.get("_csrf_token", "")
    supplied = request.form.get("csrf_token", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", supplied) or not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
        abort(400, description="The form expired. Reload the page and try again.")


@panel_bp.get("/")
def login_prompt():
    return redirect(url_for("panel.main_ui")) if current_user.is_authenticated else render_template("login.jinja2")


@panel_bp.post("/login")
def login():
    key = request.form.get("admin_key", "")
    user = attempt_login(key) if len(key) <= 256 else None
    if user is None:
        return render_template("login.jinja2", error="Invalid admin key."), 401
    session.clear()
    session.permanent = True
    login_user(user)
    return redirect(url_for("panel.main_ui"), code=303)


@panel_bp.post("/logout")
@login_required
def logout():
    revoke_session(current_user.get_id())
    logout_user()
    session.clear()
    return redirect(url_for("panel.login_prompt"), code=303)


def rows_as_files(rows):
    results = []
    for row in rows:
        item = dict(row)
        suffix = Path(item["filename"]).suffix.lower()
        item["is_image"] = suffix in IMAGE_EXTENSIONS
        item["is_video"] = suffix in VIDEO_EXTENSIONS
        item["url"] = settings().url_path + quote(item["filename"])
        if item["backed_up"]:
            item["storage"] = "B2 + cache" if item["cached"] else "B2"
        else:
            item["storage"] = "Local only" if item["cached"] else "Unavailable"
        results.append(item)
    return results


def overview(connection):
    return dict(connection.execute(
        "SELECT count(*) AS files, COALESCE(sum(CASE WHEN cached = 1 THEN size ELSE 0 END), 0) AS cached_bytes, "
        "COALESCE(sum(size), 0) AS total_bytes, COALESCE(sum(hits), 0) AS hits "
        "FROM uploaded_names WHERE status = 'available'"
    ).fetchone())


@panel_bp.get("/main")
@login_required
def main_ui():
    with closing(connect()) as connection:
        summary = overview(connection)
        recent = connection.execute("SELECT * FROM uploaded_names WHERE status = 'available' ORDER BY created DESC LIMIT 8").fetchall()
        failed = connection.execute("SELECT count(*) FROM uploaded_names WHERE status IN ('pending', 'failed')").fetchone()[0]
        staging = connection.execute("SELECT COALESCE(sum(bytes), 0) FROM staging").fetchone()[0]
    return render_template("panel.jinja2", summary=summary, files=rows_as_files(recent), failed=failed, staging=staging)


@panel_bp.get("/files")
@login_required
def files_ui():
    query = request.args.get("q", "")[:200]
    sort = request.args.get("sort", "newest")
    storage = request.args.get("storage", "all")
    order = {"newest": "created DESC", "oldest": "created ASC", "name": "filename COLLATE NOCASE", "size": "size DESC", "hits": "hits DESC"}.get(sort, "created DESC")
    try:
        page = min(100000, max(1, int(request.args.get("page", "1"))))
    except ValueError:
        raise AppError("Invalid page.")
    conditions = ["status IN ('available', 'deleting')"]
    parameters = []
    if query:
        conditions.append("(filename LIKE ? ESCAPE '\\' OR original_name LIKE ? ESCAPE '\\')")
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        parameters.extend([pattern, pattern])
    filter_condition = {"local": "backed_up = 0", "cached": "cached = 1", "remote": "backed_up = 1 AND cached = 0"}.get(storage)
    if filter_condition:
        conditions.append(filter_condition)
    where = " AND ".join(conditions)
    with closing(connect()) as connection:
        total = connection.execute(f"SELECT count(*) FROM uploaded_names WHERE {where}", parameters).fetchone()[0]
        rows = connection.execute(f"SELECT * FROM uploaded_names WHERE {where} ORDER BY {order}, filename LIMIT 25 OFFSET ?",
                                  parameters + [(page - 1) * 25]).fetchall()
    return render_template("files.jinja2", files=rows_as_files(rows), total=total, page=page, pages=max(1, math.ceil(total / 25)),
                           query=query, sort=sort, storage=storage, cursor=request.args.get("cursor", ""))


@panel_bp.get("/cache")
@login_required
def cache_files_ui():
    return redirect(url_for("panel.files_ui", storage="cached"))


@panel_bp.post("/files/sync")
@login_required
def sync_files():
    if not settings().use_b2_storage:
        raise AppError("B2 storage is disabled.")
    cursor = request.form.get("cursor") or None
    if cursor and len(cursor) > 1000:
        raise AppError("Invalid B2 cursor.")
    next_cursor = b2connect.b2_get_files(cursor=cursor)
    flash("Synchronized up to 50 files from B2. Continue to import the next batch." if next_cursor else "B2 synchronization completed.", "success")
    return redirect(url_for("panel.files_ui", cursor=next_cursor or ""), code=303)


def require_file(filename):
    cache_filepath(filename)
    row = file_record(filename)
    if not row or row["filename"] != filename or row["status"] == "deleted":
        raise AppError("File not found.", 404)
    return row


def delete_file_data(filename):
    row = require_file(filename)
    if row["backed_up"] and settings().use_b2_storage:
        b2connect.b2_delete_file(filename)
    else:
        cache_delete_file(filename)


@panel_bp.post("/files/delete/<string:filename>")
@login_required
def delete_file(filename):
    delete_file_data(filename)
    flash(f"Deleted {filename}.", "success")
    return redirect(url_for("panel.files_ui"), code=303)


@panel_bp.post("/cache/delete/<string:filename>")
@login_required
def delete_cache_file(filename):
    require_file(filename)
    cache_delete_file(filename)
    flash(f"Removed {filename} from local storage.", "success")
    return redirect(url_for("panel.files_ui"), code=303)


@panel_bp.post("/files/link/<string:filename>")
@login_required
def change_link(filename):
    row = require_file(filename)
    if row["status"] == "deleting":
        raise AppError("Finish the pending deletion before changing this link.", 409)
    with transaction() as connection:
        connection.execute("UPDATE uploaded_names SET revoked = ? WHERE filename = ?", (0 if row["revoked"] else 1, filename))
    flash("Share link restored." if row["revoked"] else "Share link revoked.", "success")
    return redirect(url_for("panel.files_ui"), code=303)


@panel_bp.post("/files/bulk")
@login_required
def bulk_files():
    filenames = list(dict.fromkeys(request.form.getlist("filename")))
    action = request.form.get("action")
    if not filenames or len(filenames) > 25 or action not in {"delete", "revoke"}:
        raise AppError("Select between 1 and 25 files and a valid action.")
    for filename in filenames:
        require_file(filename)
    if action == "delete" and request.form.get("confirmation") != "delete":
        return render_template("confirm_clear.jinja2", filenames=filenames)
    for filename in filenames:
        if action == "delete":
            delete_file_data(filename)
        else:
            with transaction() as connection:
                connection.execute("UPDATE uploaded_names SET revoked = 1 WHERE filename = ?", (filename,))
    flash(f"Updated {len(filenames)} files.", "success")
    return redirect(url_for("panel.files_ui"), code=303)


@panel_bp.get("/upload")
@login_required
def upload_ui():
    return render_template("upload.jinja2")


@panel_bp.get("/upload-auth")
def upload_auth():
    require_panel_upload_credentials()
    return "", 204


@panel_bp.post("/file-upload")
@login_required
def file_upload():
    uploads = request.files.getlist("fileupload")
    if not uploads or len(uploads) > MAX_BATCH_FILES:
        raise AppError(f"Select between 1 and {MAX_BATCH_FILES} files.")
    custom = request.form.get("file-name", "").strip()
    if custom and len(uploads) != 1:
        raise AppError("A custom filename can only be used with one file.")
    results, errors = [], []
    for index, uploaded_file in enumerate(uploads):
        try:
            if index:
                reserve_upload_attempt()
            result = actions.upload_file(uploaded_file, custom, request.form.get("expiry"))
            results.append({"filename": result.filename, "url": settings().url_path + quote(result.filename)})
        except AppError as exception:
            errors.append({"index": index, "filename": secure_original(uploaded_file.filename), "message": str(exception), "status": exception.status})
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(files=results, errors=errors), (201 if results and not errors else 207 if results else errors[0]["status"])
    for error in errors:
        flash(f"{error['filename']}: {error['message']}", "error")
    if results:
        session["upload_results"] = results[:MAX_BATCH_FILES]
        return redirect(url_for("panel.upload_result"), code=303)
    return redirect(url_for("panel.upload_ui"), code=303)


def secure_original(filename):
    return (filename or "Selected file")[:200]


@panel_bp.get("/uploaded")
@login_required
def upload_result():
    results = session.pop("upload_results", [])
    return render_template("result.jinja2", result_title="Upload complete", uploaded_files=results)


@panel_bp.post("/cache/clear")
@login_required
def clear_cache_route():
    with closing(connect()) as connection:
        local_only = connection.execute("SELECT filename FROM uploaded_names WHERE cached = 1 AND backed_up = 0").fetchall()
    if local_only and request.form.get("confirmation") != "delete":
        return render_template("confirm_clear.jinja2", filenames=[row[0] for row in local_only], clear_all=True)
    clear_cache()
    flash("Local storage cleared.", "success")
    return redirect(url_for("panel.files_ui"), code=303)


@panel_bp.get("/stats")
@login_required
def stats_ui():
    with closing(connect()) as connection:
        summary = overview(connection)
        rows = connection.execute("SELECT * FROM uploaded_names WHERE status = 'available' ORDER BY hits DESC LIMIT 100").fetchall()
    return render_template("stats.jinja2", summary=summary, files=rows_as_files(rows))


@panel_bp.get("/keys")
@login_required
def keys_ui():
    with closing(connect()) as connection:
        keys = connection.execute("SELECT id, label, created, last_used FROM upload_keys ORDER BY created DESC").fetchall()
    return render_template("keys.jinja2", keys=keys)


@panel_bp.post("/keys/create")
@login_required
def add_key():
    token = create_upload_key(request.form.get("label", ""))
    return render_template("result.jinja2", result_title="Upload key created",
                           result_outcome="Copy this upload-only key now. It will not be displayed again.", new_key=token), 201


@panel_bp.post("/keys/revoke/<string:key_id>")
@login_required
def revoke_key(key_id):
    with transaction() as connection:
        connection.execute("DELETE FROM upload_keys WHERE id = ?", (key_id,))
    flash("Upload key revoked.", "success")
    return redirect(url_for("panel.keys_ui"), code=303)

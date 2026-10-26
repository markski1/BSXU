from datetime import datetime, timezone
import hashlib
from pathlib import Path

from flask import Blueprint, Response, current_app, request, send_file, stream_with_context
from werkzeug.http import dump_options_header

from core import actions, b2connect
from core.cache import cache_filepath, publish_thumbnail, staged_file, thumbnail_path
from core.config import settings
from core.controls import lease, mark_missing, missing
from core.errors import AppError, BackendError, NotFound
from core.media import IMAGE_EXTENSIONS, MIMETYPES, process_image
from core.uploads import require_upload_key
from core.request_limits import upload_request
from core.state import count_hit, file_lock, file_record, is_deleted, transaction

file_bp = Blueprint("files", __name__)


def secure_file_response(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    sandbox = "sandbox allow-same-origin" if response.mimetype.startswith("video/") else "sandbox"
    response.headers["Content-Security-Policy"] = (
        f"{sandbox}; default-src 'none'; media-src 'self'; script-src 'none'; "
        "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    response.headers["Cache-Control"] = "no-cache"
    return response


def validate_filename(filename):
    cache_filepath(filename)
    if is_deleted(filename):
        raise NotFound()
    row = file_record(filename)
    if row and row["filename"] != filename:
        raise NotFound()


def etag_for(filename, size, created, digest=""):
    return digest or hashlib.sha256(f"{filename}:{size}:{created}".encode()).hexdigest()


def send_uploaded_file(filepath, filename):
    row = file_record(filename)
    if row is None:
        raise NotFound()
    mimetype = MIMETYPES.get(Path(filename).suffix.lower())
    response = send_file(
        filepath, mimetype=mimetype or "application/octet-stream",
        as_attachment=mimetype is None or request.args.get("download") == "1",
        download_name=filename, conditional=True,
        etag=etag_for(filename, row["size"], row["created"], row["sha256"]),
    )
    if request.method == "GET" and response.status_code == 200 and "Range" not in request.headers:
        count_hit(filename)
    return secure_file_response(response)


@file_bp.post("/upload")
def upload_file():
    config = settings()
    with upload_request():
        files = request.files.getlist("fileupload")
        if len(files) != 1:
            raise AppError("Provide exactly one file.")
        result = actions.upload_file(files[0])
    return config.url_path + result.filename, 200, {"Location": config.url_path + result.filename}


@file_bp.get("/upload-auth")
def upload_auth():
    require_upload_key()
    return "", 204


def remote_response(filename, remote):
    row = file_record(filename)
    etag = etag_for(filename, remote.size, row["created"] if row else remote.modified, row["sha256"] if row else "")
    mimetype = MIMETYPES.get(Path(filename).suffix.lower(), "application/octet-stream")
    disposition = dump_options_header(
        "attachment" if mimetype == "application/octet-stream" or request.args.get("download") == "1" else "inline",
        {"filename": filename},
    )
    headers = {"Content-Disposition": disposition, "Accept-Ranges": "bytes"}
    modified = datetime.fromtimestamp(remote.modified, timezone.utc)
    response = Response(mimetype=mimetype, headers=headers)
    response.set_etag(etag)
    response.last_modified = modified
    response.content_length = remote.size
    response.make_conditional(request, accept_ranges=True, complete_length=remote.size)
    if response.status_code in {304, 412}:
        response.content_length = 0
        return secure_file_response(response)
    if request.method == "HEAD" or remote.size == 0:
        return secure_file_response(response)

    partial = response.status_code == 206
    start = response.content_range.start if partial else 0
    stop = response.content_range.stop if partial else remote.size
    slot = lease("downloads", 8)
    slot.__enter__()
    download = None
    try:
        download = b2connect.bucket().download_file_by_name(
            file_name=filename, range_=(start, stop - 1) if partial else None,
        )
        declared = download.response.headers.get("Content-Length")
        if declared is not None and int(declared) != stop - start:
            raise BackendError("The remote file changed. Please retry.")
    except Exception as exception:
        try:
            if download is not None:
                download.response.close()
        finally:
            slot.__exit__(type(exception), exception, exception.__traceback__)
        if isinstance(exception, AppError):
            raise
        raise BackendError() from exception
    app = current_app._get_current_object()
    closed = False

    def close():
        nonlocal closed
        if not closed:
            closed = True
            try:
                download.response.close()
            finally:
                with app.app_context():
                    slot.__exit__(None, None, None)

    def chunks():
        total = 0
        try:
            for chunk in download.response.iter_content(64 * 1024):
                total += len(chunk)
                if total > stop - start:
                    raise BackendError("B2 returned more bytes than expected.")
                yield chunk
            if total != stop - start:
                raise BackendError("B2 returned an incomplete file.")
            if response.status_code == 200 and "Range" not in request.headers:
                count_hit(filename)
        finally:
            close()

    response.response = stream_with_context(chunks())
    response.call_on_close(close)
    return secure_file_response(response)


@file_bp.route("/<string:filename>", methods=["GET", "HEAD"])
def get_file(filename):
    validate_filename(filename)
    target = cache_filepath(filename)
    if target.is_file():
        try:
            return send_uploaded_file(target, filename)
        except FileNotFoundError:
            with transaction() as connection:
                connection.execute("UPDATE uploaded_names SET cached = 0 WHERE filename = ?", (filename,))
    if not settings().use_b2_storage:
        raise NotFound()
    with lease("metadata", 8):
        remote = b2connect.b2_file_info(filename)
    if remote is None:
        raise NotFound()
    b2connect.remember_remote(remote)
    if request.method != "HEAD" and "Range" not in request.headers and remote.size <= 8 * 1024 * 1024:
        try:
            with lease("downloads", 8):
                target = b2connect.b2_cache_file(filename, remote)
            return send_uploaded_file(target, filename)
        except AppError as exception:
            if isinstance(exception, BackendError) or exception.status not in {503, 507}:
                raise
        except FileNotFoundError:
            pass
    return remote_response(filename, remote)


@file_bp.route("/thumbnail/<string:filename>", methods=["GET", "HEAD"])
def get_file_thumbnail(filename):
    validate_filename(filename)
    if Path(filename).suffix.lower() not in IMAGE_EXTENSIONS:
        raise AppError("A thumbnail is not available for this file.", 415)
    preview = thumbnail_path(filename)
    if preview.is_file():
        return secure_file_response(send_file(preview, mimetype="image/png", conditional=True))
    if missing("thumbnail:" + filename):
        raise NotFound()
    target = cache_filepath(filename)
    if not target.is_file():
        raise NotFound()
    if request.method == "HEAD":
        raise NotFound()
    with file_lock(filename):
        if not preview.is_file():
            maximum = settings().max_upload_size_mb * 1024 * 1024
            with staged_file(maximum * 3) as temporary:
                with target.open("rb") as source:
                    actions.copy_bounded(source, temporary, maximum)
                try:
                    _, thumbnail = process_image(temporary, target.suffix.lower())
                    publish_thumbnail(thumbnail, filename)
                except AppError:
                    mark_missing("thumbnail:" + filename)
                    raise
    return secure_file_response(send_file(preview, mimetype="image/png", conditional=True))

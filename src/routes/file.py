from datetime import datetime, timezone
import hashlib
from pathlib import Path
import time

from flask import Blueprint, Response, current_app, request, send_file, stream_with_context
from werkzeug.http import dump_options_header, parse_if_range_header, parse_range_header
from werkzeug.utils import secure_filename

from core import actions, b2connect
from core.cache import cache_filepath, publish_thumbnail, staged_file, thumbnail_path
from core.config import settings
from core.controls import lease, mark_missing, missing, renew_lease
from core.errors import AppError, BackendError, NotFound
from core.media import IMAGE_EXTENSIONS, MIMETYPES, process_image
from core.uploads import require_upload_key
from core.request_limits import upload_request
from core.state import file_record, is_deleted, transaction
from core.stats import count_hit

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
    if secure_filename(filename) != filename:
        raise AppError("Invalid filename.")
    if is_deleted(filename):
        raise NotFound()
    row = file_record(filename)
    if row and row["filename"] != filename:
        raise NotFound()


def etag_for(filename, size, created, digest=""):
    return digest or hashlib.sha256(f"{filename}:{size}:{created}".encode()).hexdigest()


def send_uploaded_file(filepath, filename):
    row = file_record(filename)
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
    if request.if_none_match.contains(etag):
        response.status_code = 304
        return secure_file_response(response)
    start, stop = 0, remote.size
    range_header = request.headers.get("Range")
    if range_header and request.headers.get("If-Range"):
        condition = parse_if_range_header(request.headers["If-Range"])
        if (condition.etag and condition.etag != etag) or (condition.date and modified > condition.date):
            range_header = None
    if range_header:
        parsed = parse_range_header(range_header)
        bounds = parsed.range_for_length(remote.size) if parsed and len(parsed.ranges) == 1 else None
        if bounds is None:
            response.status_code = 416
            response.headers["Content-Range"] = f"bytes */{remote.size}"
            response.content_length = 0
            return secure_file_response(response)
        start, stop = bounds
        response.status_code = 206
        response.headers["Content-Range"] = f"bytes {start}-{stop - 1}/{remote.size}"
        response.content_length = stop - start
    if request.method == "HEAD" or remote.size == 0:
        return secure_file_response(response)

    slot = lease("downloads", 8)
    slot_token = slot.__enter__()
    try:
        download = b2connect.bucket().download_file_by_name(
            file_name=filename, range_=(start, stop - 1) if range_header else None,
        )
        declared = download.response.headers.get("Content-Length")
        if declared is not None and int(declared) != stop - start:
            download.response.close()
            raise BackendError("The remote file changed. Please retry.")
    except Exception as exception:
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
            download.response.close()
            with app.app_context():
                slot.__exit__(None, None, None)

    def chunks():
        total, renewed = 0, time.monotonic()
        try:
            for chunk in download.response.iter_content(64 * 1024):
                total += len(chunk)
                if total > stop - start:
                    raise BackendError("B2 returned more bytes than expected.")
                if time.monotonic() - renewed > 30:
                    renew_lease(slot_token)
                    renewed = time.monotonic()
                yield chunk
            if total != stop - start:
                raise BackendError("B2 returned an incomplete file.")
            if response.status_code == 200 and not range_header:
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
        with lease("downloads", 8):
            target = b2connect.b2_cache_file(filename, remote)
        return send_uploaded_file(target, filename)
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
    from core.state import file_lock
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

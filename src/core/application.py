from datetime import timedelta
import json
import logging
import secrets
import time
from urllib.parse import urlsplit

import click
from flask import Flask, g, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from core.config import Settings
from core.controls import rate_limit
from core.errors import AppError
from core.request_limits import UploadRequest, upload_request
from core.uploads import UPLOAD_ENDPOINTS, authorize_upload_request, reserve_upload_attempt


class LoopbackProxy:
    def __init__(self, app):
        self.app = app
        self.forwarded = ProxyFix(app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)

    def __call__(self, environ, start_response):
        handler = self.forwarded if environ.get("REMOTE_ADDR") in {"127.0.0.1", "::1"} else self.app
        return handler(environ, start_response)


def create_app(overrides=None):
    config = Settings.load(overrides)
    app = Flask(__name__, static_folder="../webpanel/static", template_folder="../webpanel/templates")
    app.request_class = UploadRequest
    app.jinja_env.autoescape = True
    app.logger.setLevel(logging.INFO)
    app.extensions["settings"] = config
    app.config.update(
        SECRET_KEY=config.secret_key,
        MAX_CONTENT_LENGTH=(config.max_upload_size_mb + 1) * 1024 * 1024,
        MAX_FORM_MEMORY_SIZE=64 * 1024, MAX_FORM_PARTS=20,
        SESSION_COOKIE_SECURE=config.cookie_secure, SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_NAME="bsxu_session_v2",
        SESSION_COOKIE_PATH="/panel", PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        TRUSTED_HOSTS=[urlsplit(config.url_path).hostname], TESTING=bool((overrides or {}).get("TESTING", False)),
    )
    if str((overrides or {}).get("TRUST_PROXY_HEADERS", "false")).lower() == "true" or (
            overrides is None and __import__("os").getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"):
        app.wsgi_app = LoopbackProxy(app.wsgi_app)
    from core import b2connect, cache, session, state
    from routes.file import file_bp
    from routes.panel import panel_bp
    with app.app_context():
        state.init_app(app)
        cache.init_cache()
    session.init_app(app)
    b2connect.init_app(app)
    app.register_blueprint(file_bp)
    if config.panel_enabled:
        app.register_blueprint(panel_bp)

    @app.get("/")
    def index():
        return redirect(url_for("panel.login_prompt")) if config.panel_enabled else ("BSXU", 200)

    @app.get("/health")
    def health():
        from contextlib import closing
        import shutil
        with closing(state.connect()) as connection:
            connection.execute("SELECT 1").fetchone()
        ready = shutil.disk_usage(config.cache_folder).free >= config.min_free_disk_mb * 1024 * 1024
        return jsonify(status="ok" if ready else "low-disk"), 200 if ready else 503

    @app.before_request
    def guard_request():
        g.request_id = secrets.token_hex(8)
        g.started = time.monotonic()
        endpoint = request.endpoint or ""
        identifier = request.remote_addr or "unknown"
        if request.mimetype == "multipart/form-data" and endpoint not in UPLOAD_ENDPOINTS:
            raise AppError("File data is accepted only by authorized upload endpoints.", 415)
        if endpoint.startswith("panel.") and endpoint != "panel.file_upload" and request.method == "POST":
            request.max_content_length = 64 * 1024
        if endpoint in UPLOAD_ENDPOINTS:
            rate_limit("upload-request-global", "all", 30)
            rate_limit("upload-request", identifier, 5)
            authorize_upload_request()
            reserve_upload_attempt()
        elif endpoint in {"files.upload_auth", "panel.upload_auth"}:
            rate_limit("upload-preflight-global", "all", 30)
            rate_limit("upload-preflight", identifier, 5)
        elif endpoint.startswith("files."):
            rate_limit("public-global", "all", 600)
            rate_limit("public", identifier, 120)
            if endpoint == "files.get_file_thumbnail":
                rate_limit("thumbnail-global", "all", 180)
                rate_limit("thumbnail", identifier, 60)
        if endpoint == "panel.login":
            rate_limit("login-global", "all", 100, 300)
            rate_limit("login", identifier, 5, 300)
        if endpoint == "panel.file_upload":
            slot = upload_request()
            slot.__enter__()
            g.upload_slot = slot

    @app.teardown_request
    def release_request(exception):
        slot = g.pop("upload_slot", None)
        if slot is not None:
            slot.__exit__(None, None, None)

    @app.after_request
    def secure_response(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Request-ID"] = g.get("request_id", "")
        if request.path.startswith("/panel"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
                "media-src 'self' blob:; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
            )
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if response.status_code in {429, 503}:
            response.headers["Retry-After"] = str(g.get("retry_after", 60))
        app.logger.info(json.dumps({
            "request_id": g.get("request_id"), "method": request.method,
            "path": request.path, "status": response.status_code,
            "duration_ms": round((time.monotonic() - g.get("started", time.monotonic())) * 1000),
        }))
        return response

    @app.errorhandler(AppError)
    def handle_application_error(error):
        if error.retry_after is not None:
            g.retry_after = error.retry_after
        if config.panel_enabled and request.path.startswith("/panel") and request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return render_template("result.jinja2", result_title="Request failed", result_outcome=str(error)), error.status
        return str(error), error.status, {"Content-Type": "text/plain; charset=utf-8"}

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        if config.panel_enabled and request.path.startswith("/panel"):
            return render_template("result.jinja2", result_title=error.name, result_outcome=error.description), error.code
        return error.description, error.code, {"Content-Type": "text/plain; charset=utf-8"}

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        from misc import wh_report
        wh_report("Unhandled request error.", error)
        return handle_application_error(AppError("The request failed. Please retry or check the server log.", 500))

    @app.template_filter("filesize")
    def filesize(value):
        value = int(value or 0)
        for unit in ("B", "KiB", "MiB", "GiB"):
            if value < 1024 or unit == "GiB":
                return f"{value:,} B" if unit == "B" else f"{value:,.1f} {unit}"
            value /= 1024

    @app.template_filter("date")
    def date(value):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if value else "—"

    @app.cli.command("create-upload-key")
    @click.argument("label")
    def create_upload_key_command(label):
        """Create a named upload-only key and display it once."""
        try:
            key = session.create_upload_key(label)
        except AppError as exception:
            raise click.ClickException(str(exception)) from exception
        click.echo(key)

    @app.cli.command("maintenance")
    def maintenance():
        from core.actions import recover_pending
        with app.app_context():
            cache.recover_staging()
            cache.reconcile_cache()
            cache.cleanup_deleted()
            recover_pending()
        print("Storage reconciliation and abandoned upload recovery completed.")

    return app

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from flask import g
from werkzeug.datastructures import FileStorage
from werkzeug.test import EnvironBuilder

from core.application import create_app
from core import actions, b2connect, cache, controls, state
from core.config import Settings
from core.errors import AppError, BackendError
from core.session import create_upload_key
from core.uploads import reserve_upload_attempt


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(__file__).resolve().parents[2]
        self.temporary = tempfile.TemporaryDirectory(prefix="bsxu-test-", dir=self.workspace)
        self.root = Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(self.workspace))
        self.config = {
            "ADMIN_KEY": "a" * 40, "SECRET_KEY": "s" * 40,
            "URL_PATH": "https://localhost/", "CACHE_FOLDER": str(self.root / "cache"),
            "STATE_FOLDER": str(self.root / "state"), "MIN_FREE_DISK_MB": 0,
            "MAX_UPLOAD_SIZE_MB": 1, "CACHE_MAX_SIZE_MB": 4, "STAGING_MAX_SIZE_MB": 16,
            "USE_B2_STORAGE": "false", "PANEL_ENABLED": "true", "WEBHOOK_URL": "false",
            "SESSION_COOKIE_SECURE": "true", "TESTING": True,
        }
        self.app = create_app(self.config)
        self.app.logger.disabled = True
        with self.app.app_context():
            self.upload_key = create_upload_key("Test uploader")
        self.client = self.app.test_client()
        self.responses = []

    def tearDown(self):
        for response in self.responses:
            response.close()
        self.temporary.cleanup()

    def get(self, path, **kwargs):
        response = self.client.get(path, base_url="https://localhost", **kwargs)
        self.responses.append(response)
        return response

    def post(self, path, **kwargs):
        response = self.client.post(path, base_url="https://localhost", **kwargs)
        response.request.environ["wsgi.input"].close()
        self.responses.append(response)
        return response

    def probe_upload_body(self, path, headers=None, address="127.0.0.1", fields=None):
        class CountedBody(io.BytesIO):
            count = 0

            def read(self, *args):
                value = super().read(*args)
                self.count += len(value)
                return value

            def readinto(self, buffer):
                count = super().readinto(buffer)
                self.count += count
                return count

        data = {"fileupload": (io.BytesIO(b"x" * 700000), "fixture.bin")} | (fields or {})
        builder = EnvironBuilder(path=path, method="POST", base_url="https://localhost", data=data, headers=headers)
        environ = builder.get_environ()
        source = environ["wsgi.input"]
        stream = CountedBody(source.read())
        source.close()
        environ["wsgi.input"] = stream
        environ["REMOTE_ADDR"] = address
        response = self.client.open(environ)
        self.responses.append(response)
        response.request.environ["wsgi.input"].close()
        builder.close()
        return response, stream.count

    def assert_no_upload_started(self):
        with self.app.app_context(), closing(state.connect()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM staging").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM uploaded_names").fetchone()[0], 0)
        with self.app.app_context(), closing(state.connect("controls.sqlite3")) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM upload_events").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM leases").fetchone()[0], 0)
        self.assertFalse(list((self.root / "cache" / ".staging").iterdir()))

    def csrf(self, path="/panel/"):
        response = self.get(path)
        return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)

    def login(self):
        return self.post("/panel/login", data={"csrf_token": self.csrf(), "admin_key": "a" * 40})

    def upload(self, content=b"hello", name="fixture.txt"):
        return self.post("/upload", headers={"Authorization": "Bearer " + self.upload_key},
                         data={"fileupload": (io.BytesIO(content), name)})

    def stored_name(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.text.rsplit("/", 1)[1]

    def enable_b2(self, bucket):
        self.app.extensions["settings"] = replace(self.app.extensions["settings"], use_b2_storage=True)
        self.app.extensions["b2_bucket"] = bucket

    def test_settings_reject_weak_credentials_and_invalid_booleans(self):
        for override in [{"ADMIN_KEY": "ChangeMe!"}, {"SECRET_KEY": "short"}, {"USE_B2_STORAGE": "true|false"}]:
            with self.assertRaises(RuntimeError):
                Settings.load(self.config | override)

    def test_authentication_rejects_before_body_read(self):
        class CountingStream(io.BytesIO):
            count = 0

            def readinto(self, buffer):
                count = super().readinto(buffer)
                self.count += count
                return count

            def read(self, *args):
                value = super().read(*args)
                self.count += len(value)
                return value

        for key in ("", "wrong"):
            builder = EnvironBuilder(path="/upload", method="POST", base_url="https://localhost",
                                     data={"fileupload": (io.BytesIO(b"x" * 700000), "fixture.bin")})
            environ = builder.get_environ()
            stream = CountingStream(environ["wsgi.input"].read())
            environ["wsgi.input"] = stream
            if key:
                environ["HTTP_AUTHORIZATION"] = "Bearer " + key
            with self.app.request_context(environ):
                response = self.app.full_dispatch_request()
                self.assertEqual(response.status_code, 401)
                self.assertEqual(stream.count, 0)
            builder.close()

    def test_retired_environment_key_cannot_authorize_uploads(self):
        legacy_key = "legacy-upload-key-" + "x" * 32
        self.app = create_app(self.config | {"AUTH_KEY": legacy_key})
        self.app.logger.disabled = True
        self.client = self.app.test_client()
        for headers in ({"Authorization": "Bearer " + legacy_key}, {"X-Upload-Key": legacy_key}):
            response, count = self.probe_upload_body("/upload", headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(count, 0)
            self.assertEqual(self.get("/upload-auth", headers=headers).status_code, 401)
        self.assert_no_upload_started()
        self.assertEqual(self.upload().status_code, 200)

    def test_secret_key_is_required_without_panel(self):
        for secret in ("", "short"):
            with self.assertRaisesRegex(RuntimeError, "SECRET_KEY"):
                Settings.load(self.config | {"PANEL_ENABLED": "false", "ADMIN_KEY": "", "SECRET_KEY": secret})

    def test_storage_paths_are_independent_of_working_directory(self):
        expected = Path(__file__).resolve().parents[1]
        config = self.config | {"CACHE_FOLDER": "cache", "STATE_FOLDER": "instance"}
        with patch("os.getcwd", return_value=str(self.root)):
            first = Settings.load(config)
        with patch("os.getcwd", return_value=str(self.workspace)):
            second = Settings.load(config)
        self.assertEqual(first.cache_folder, expected / "cache")
        self.assertEqual(first.state_folder, expected / "instance")
        self.assertEqual(first.cache_folder, second.cache_folder)
        self.assertEqual(first.state_folder, second.state_folder)

    def test_upload_key_cli_bootstraps_without_panel_and_survives_restart(self):
        config = self.config | {"PANEL_ENABLED": "false", "ADMIN_KEY": ""}
        app = create_app(config)
        app.logger.disabled = True
        result = app.test_cli_runner().invoke(args=["create-upload-key", "Headless uploader"])
        self.assertEqual(result.exit_code, 0, result.output)
        key = result.output.strip()
        self.assertRegex(key, r"^[A-Za-z0-9_-]{43}$")
        self.app = create_app(config)
        self.app.logger.disabled = True
        self.client = self.app.test_client()
        self.assertEqual(self.get("/panel/keys").status_code, 404)
        self.assertEqual(self.get("/upload-auth", headers={"X-Upload-Key": key}).status_code, 204)
        response = self.post("/upload", headers={"Authorization": "Bearer " + key},
                             data={"fileupload": (io.BytesIO(b"hello"), "fixture.txt")})
        self.assertEqual(response.status_code, 200)
        with self.app.app_context(), closing(state.connect()) as connection:
            row = connection.execute("SELECT token_hash FROM upload_keys WHERE label = 'Headless uploader'").fetchone()
            self.assertIsNotNone(row)
            self.assertNotEqual(row["token_hash"], key)
            self.assertNotIn(key.encode(), (self.root / "state" / "storage.sqlite3").read_bytes())

    def test_upload_key_creation_rejects_invalid_labels(self):
        runner = self.app.test_cli_runner()
        for label in (" ", "x" * 81):
            result = runner.invoke(args=["create-upload-key", label])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("between 1 and 80 characters", result.output)

    def test_legacy_login_field_does_not_authenticate(self):
        response = self.post("/panel/login", data={"csrf_token": self.csrf(), "auth_key": "a" * 40})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.get("/panel/files").status_code, 302)

    def test_successful_upload_and_meaningful_errors(self):
        name = self.stored_name(self.upload())
        self.assertRegex(name, r"^[A-Za-z0-9]{12}\.txt$")
        self.assertEqual(self.get("/" + name).data, b"hello")
        self.assertEqual(self.post("/upload", headers={"X-Upload-Key": self.upload_key}).status_code, 400)
        self.assertEqual(self.upload(b"", "empty.txt").status_code, 400)
        self.assertEqual(self.upload(b"x", "noextension").status_code, 400)

    def test_upload_limit_and_staging_cleanup(self):
        self.assertEqual(self.upload(b"x" * (1024 * 1024 + 1)).status_code, 413)
        with self.app.app_context(), closing(state.connect()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM staging").fetchone()[0], 0)
        self.assertFalse(list((self.root / "cache" / ".staging").iterdir()))

    def test_upload_key_cannot_sign_in(self):
        token = self.csrf()
        self.assertEqual(self.post("/panel/login", data={"csrf_token": token, "admin_key": self.upload_key}).status_code, 401)
        self.assertEqual(self.get("/panel/files").status_code, 302)

    def test_admin_key_cannot_upload_through_api(self):
        response = self.post("/upload", headers={"Authorization": "Bearer " + "a" * 40},
                             data={"fileupload": (io.BytesIO(b"x"), "fixture.txt")})
        self.assertEqual(response.status_code, 401)

    def test_cookie_and_panel_headers(self):
        response = self.get("/panel/")
        cookie = response.headers["Set-Cookie"]
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/panel", cookie)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_csrf_missing_and_unicode_are_client_errors(self):
        self.login()
        self.assertEqual(self.post("/panel/cache/clear").status_code, 400)
        self.assertEqual(self.post("/panel/cache/clear", data={"csrf_token": "malformed-é"}).status_code, 400)

    def test_unauthorized_panel_upload_does_not_parse_body(self):
        response = self.post("/panel/file-upload", data={"fileupload": (io.BytesIO(b"x" * 700000), "fixture.bin")})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(list((self.root / "cache" / ".staging").iterdir()))

    def test_login_rate_limit(self):
        token = self.csrf()
        responses = [self.post("/panel/login", data={"csrf_token": token, "admin_key": "wrong"}) for _ in range(6)]
        self.assertEqual([r.status_code for r in responses], [401] * 5 + [429])
        self.assertIn("Retry-After", responses[-1].headers)

    def test_login_body_is_small(self):
        response = self.post("/panel/login", data={"admin_key": "x" * 70000})
        self.assertEqual(response.status_code, 413)

    def test_logout_redirects_to_login_and_revokes_session(self):
        self.login()
        token = self.csrf("/panel/main")
        response = self.post("/panel/logout", data={"csrf_token": token})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/panel/")
        self.assertEqual(self.get("/panel/files").status_code, 302)

    def test_multiple_admin_sessions_are_independent(self):
        self.login()
        second = self.app.test_client()
        response = second.get("/panel/", base_url="https://localhost")
        token = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
        second.post("/panel/login", data={"csrf_token": token, "admin_key": "a" * 40}, base_url="https://localhost")
        self.assertEqual(self.get("/panel/main").status_code, 200)

    def test_containment_and_alias_paths_are_rejected(self):
        with self.app.app_context():
            for name in ["../file", "..\\file", "C:file", ".env", "trailing.", "a\r\nb"]:
                with self.assertRaises(AppError):
                    cache.cache_filepath(name)
        self.assertIn(self.get("/some%20file.txt").status_code, {400, 404})

    def test_active_content_is_downloaded_and_video_remains_inline(self):
        for extension in ("html", "svg", "txt"):
            name = self.stored_name(self.upload(b"<script>alert(1)</script>", "fixture." + extension))
            response = self.get("/" + name)
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertEqual(response.mimetype, "application/octet-stream")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("script-src 'none'", response.headers["Content-Security-Policy"])
        name = self.stored_name(self.upload(b"0123456789", "fixture.mp4"))
        response = self.get("/" + name, headers={"Range": "bytes=2-4"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b"234")
        self.assertEqual(response.mimetype, "video/mp4")
        self.assertIn("inline", response.headers["Content-Disposition"])
        self.assertIn("attachment", self.get("/" + name + "?download=1").headers["Content-Disposition"])

    def test_nonimage_thumbnail_never_returns_original(self):
        name = self.stored_name(self.upload(b"private original", "fixture.mp4"))
        response = self.get("/thumbnail/" + name)
        self.assertEqual(response.status_code, 415)
        self.assertNotIn(b"private original", response.data)

    def test_jpeg_orientation_and_metadata(self):
        image = Image.new("RGB", (8, 4), "red")
        exif = Image.Exif()
        exif[274], exif[315] = 6, "PRIVATE ARTIST"
        original = io.BytesIO()
        image.save(original, format="JPEG", exif=exif)
        name = self.stored_name(self.upload(original.getvalue(), "fixture.jpg"))
        response = self.get("/" + name)
        with Image.open(io.BytesIO(response.data)) as cleaned:
            self.assertEqual(cleaned.size, (4, 8))
            self.assertIsNone(cleaned.getexif().get(315))

    def test_gif_animation_is_preserved_and_thumbnail_is_cached(self):
        original = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(original, format="GIF", save_all=True,
                    append_images=[Image.new("RGB", (8, 8), "blue")], duration=[100, 200], loop=0)
        name = self.stored_name(self.upload(original.getvalue(), "fixture.gif"))
        with Image.open(io.BytesIO(self.get("/" + name).data)) as cleaned:
            self.assertEqual(cleaned.n_frames, 2)
            self.assertEqual(cleaned.info["duration"], 100)
        with patch("routes.file.process_image", side_effect=AssertionError("cached thumbnails must not decode again")):
            first = self.get("/thumbnail/" + name)
            second = self.get("/thumbnail/" + name)
        self.assertEqual(first.mimetype, "image/png")
        self.assertEqual(first.data, second.data)

    def test_malformed_images_fail_closed(self):
        self.assertEqual(self.upload(b"not a PNG", "fixture.png").status_code, 422)
        self.assertEqual([p.name for p in (self.root / "cache").iterdir()], [".staging"])
        with self.app.app_context(), closing(state.connect()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM uploaded_names").fetchone()[0], 0)

    def test_image_processing_timeout_is_bounded(self):
        from subprocess import TimeoutExpired
        with patch("core.media.subprocess.run", side_effect=TimeoutExpired("worker", 20)):
            self.assertEqual(self.upload(b"fixture", "fixture.jpg").status_code, 422)

    def test_stage_reservations_are_shared_between_threads(self):
        self.app.extensions["settings"] = replace(self.app.extensions["settings"], staging_max_size_mb=1)
        acquired, release = threading.Event(), threading.Event()

        def first():
            with self.app.app_context(), cache.staged_file(1024 * 1024):
                acquired.set()
                release.wait(5)

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(first)
            self.assertTrue(acquired.wait(5))
            try:
                with self.app.app_context(), self.assertRaises(AppError) as caught:
                    with cache.staged_file(1):
                        pass
                self.assertEqual(caught.exception.status, 503)
            finally:
                release.set()
            future.result()

    def test_cache_capacity_preserves_local_primary_files(self):
        self.app.extensions["settings"] = replace(self.app.extensions["settings"], cache_max_size_mb=1)
        first = self.stored_name(self.upload(b"a" * 600000))
        self.assertEqual(self.upload(b"b" * 600000).status_code, 507)
        self.assertEqual(len(self.get("/" + first).data), 600000)

    def test_expired_staging_is_recovered(self):
        directory = self.root / "cache" / ".staging" / ("a" * 32)
        directory.mkdir()
        (directory / "upload.part").write_bytes(b"orphan")
        with self.app.app_context():
            with state.transaction() as connection:
                connection.execute("INSERT INTO staging VALUES (?, ?, ?)", ("a" * 32, 6, time.time() - 1))
            cache.recover_staging()
        self.assertFalse(directory.exists())

    def test_reconciliation_frees_capacity_for_missing_local_files(self):
        self.app.extensions["settings"] = replace(self.app.extensions["settings"], cache_max_size_mb=1)
        name = self.stored_name(self.upload(b"a" * 600000))
        (self.root / "cache" / name).unlink()
        with self.app.app_context():
            cache.reconcile_cache()
            row = state.file_record(name)
            self.assertEqual(row["cached"], 0)
            self.assertEqual(row["status"], "available")
        self.assertEqual(self.upload(b"b" * 600000).status_code, 200)
        self.login()
        self.assertIn("Unavailable", self.get("/panel/files").text)

    def test_unindexed_local_file_returns_not_found(self):
        (self.root / "cache" / "unindexed.txt").write_bytes(b"not imported")
        self.assertEqual(self.get("/unindexed.txt").status_code, 404)

    def test_public_misses_are_cached_and_locks_are_bounded(self):
        bucket = Mock()
        bucket.get_file_info_by_name.side_effect = b2connect.FileNotPresent("missing")
        self.enable_b2(bucket)
        for _ in range(3):
            self.assertEqual(self.get("/missing.png").status_code, 404)
        self.assertEqual(bucket.get_file_info_by_name.call_count, 1)
        with self.app.app_context():
            for index in range(100):
                with state.file_lock("miss-" + str(index)):
                    pass
        self.assertLessEqual(len(list((self.root / "state" / "locks-v2").glob("*.lock"))), 64)

    def test_negative_cache_is_bounded(self):
        with self.app.app_context():
            with state.transaction("controls.sqlite3") as connection:
                connection.executemany("INSERT INTO misses VALUES (?, ?)", [(str(index), time.time() + 60) for index in range(4100)])
            controls.mark_missing("extra")
            with closing(state.connect("controls.sqlite3")) as connection:
                self.assertLessEqual(connection.execute("SELECT count(*) FROM misses").fetchone()[0], 4096)

    def test_backend_outage_is_not_a_404(self):
        bucket = Mock()
        bucket.get_file_info_by_name.side_effect = RuntimeError("offline")
        self.enable_b2(bucket)
        self.assertEqual(self.get("/remote.txt").status_code, 503)

    def test_cold_video_head_and_range_do_not_download_whole_file(self):
        bucket = Mock()
        bucket.get_file_info_by_name.return_value = SimpleNamespace(
            content_length=10 * 1024 * 1024, file_info={}, upload_timestamp=1700000000000)
        stream = Mock()
        stream.headers = {"Content-Length": "10"}
        stream.iter_content.return_value = [b"0123456789"]
        bucket.download_file_by_name.return_value = SimpleNamespace(response=stream)
        self.enable_b2(bucket)
        head = self.client.head("/remote.mp4", base_url="https://localhost")
        self.responses.append(head)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content_length, 10 * 1024 * 1024)
        bucket.download_file_by_name.assert_not_called()
        response = self.get("/remote.mp4", headers={"Range": "bytes=10-19"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b"0123456789")
        self.assertEqual(response.headers["Content-Range"], "bytes 10-19/10485760")
        bucket.download_file_by_name.assert_called_once_with(file_name="remote.mp4", range_=(10, 19))
        self.assertFalse((self.root / "cache" / "remote.mp4").exists())
        with self.app.app_context(), closing(state.connect("controls.sqlite3")) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM leases").fetchone()[0], 0)

    def test_invalid_remote_range_returns_416(self):
        bucket = Mock()
        bucket.get_file_info_by_name.return_value = SimpleNamespace(content_length=100, file_info={}, upload_timestamp=1700000000000)
        self.enable_b2(bucket)
        self.assertEqual(self.get("/remote.mp4", headers={"Range": "bytes=1000-2000"}).status_code, 416)
        bucket.download_file_by_name.assert_not_called()

    def test_failed_custom_upload_can_be_retried(self):
        bucket = Mock()
        bucket.get_file_info_by_name.side_effect = b2connect.FileNotPresent("missing")
        bucket.upload_local_file.side_effect = [RuntimeError("offline"), SimpleNamespace()]
        self.enable_b2(bucket)
        with self.app.app_context():
            with self.assertRaises(BackendError):
                actions.upload_file(FileStorage(io.BytesIO(b"hello"), filename="fixture.txt"), "retry")
            self.assertEqual(state.file_record("retry.txt")["status"], "failed")
            result = actions.upload_file(FileStorage(io.BytesIO(b"hello"), filename="fixture.txt"), "retry")
            self.assertEqual(result.filename, "retry.txt")
            self.assertEqual(state.file_record("retry.txt")["status"], "available")

    def test_uncertain_remote_success_is_recovered_without_overwrite(self):
        bucket = Mock()
        digest = hashlib.sha256(b"hello").hexdigest()
        bucket.get_file_info_by_name.side_effect = [
            b2connect.FileNotPresent("missing"),
            SimpleNamespace(content_length=5, file_info={"bsxu_sha256": digest}, upload_timestamp=1700000000000),
        ]
        bucket.upload_local_file.side_effect = RuntimeError("confirmation lost")
        self.enable_b2(bucket)
        with self.app.app_context():
            with self.assertRaises(BackendError):
                actions.upload_file(FileStorage(io.BytesIO(b"hello"), filename="fixture.txt"), "retry")
            result = actions.upload_file(FileStorage(io.BytesIO(b"hello"), filename="fixture.txt"), "retry")
            self.assertEqual(result.filename, "retry.txt")
            self.assertEqual(state.file_record("retry.txt")["status"], "available")
        self.assertEqual(bucket.upload_local_file.call_count, 1)

    def test_b2_sync_is_paginated_and_understands_list_sizes(self):
        bucket = Mock()
        bucket.id_ = "bucket-id"
        bucket.api.session.list_file_names.return_value = {
            "files": [{"fileName": f"remote-{index:03d}.txt", "size": 10, "fileInfo": {}, "uploadTimestamp": 1700000000000} for index in range(51)]
        }
        self.enable_b2(bucket)
        with self.app.app_context():
            cursor = b2connect.b2_get_files()
            self.assertEqual(cursor, "remote-050.txt")
            with closing(state.connect()) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM uploaded_names").fetchone()[0], 50)

    def test_link_revocation_and_expiry_block_conditional_requests(self):
        name = self.stored_name(self.upload())
        original = self.get("/" + name)
        self.login()
        token = self.csrf("/panel/files")
        self.assertEqual(self.post("/panel/files/link/" + name, data={"csrf_token": token}).status_code, 303)
        self.assertEqual(self.get("/" + name, headers={"If-None-Match": original.headers["ETag"]}).status_code, 404)
        with self.app.app_context(), state.transaction() as connection:
            connection.execute("UPDATE uploaded_names SET revoked = 0, expires = ? WHERE filename = ?", (time.time() - 1, name))
        self.assertEqual(self.get("/" + name).status_code, 404)

    def test_upload_key_is_upload_only_and_revocable(self):
        self.login()
        token = self.csrf("/panel/keys")
        result = self.post("/panel/keys/create", data={"csrf_token": token, "label": "Laptop"})
        self.assertEqual(result.status_code, 201)
        self.assertIn("Upload key created", result.text)
        key = re.search(r"<code>([A-Za-z0-9_-]{43})</code>", result.text).group(1)
        listing = self.get("/panel/keys")
        self.assertIn("Upload keys", listing.text)
        self.assertIn("Laptop", listing.text)
        self.assertNotIn(key, listing.text)
        response = self.post("/upload", headers={"X-Upload-Key": key},
                             data={"fileupload": (io.BytesIO(b"hello"), "fixture.txt")})
        self.assertEqual(response.status_code, 200)
        with self.app.app_context(), closing(state.connect()) as connection:
            key_id = connection.execute("SELECT id FROM upload_keys WHERE label = 'Laptop'").fetchone()[0]
        result = self.post("/panel/keys/revoke/" + key_id, data={"csrf_token": token})
        self.assertEqual(result.status_code, 303)
        self.assertEqual(self.post("/upload", headers={"X-Upload-Key": key}).status_code, 401)
        self.assertEqual(self.upload().status_code, 200)

    def test_persistent_counts_exclude_head_range_and_304(self):
        name = self.stored_name(self.upload(b"0123456789", "fixture.mp4"))
        first = self.get("/" + name)
        head = self.client.head("/" + name, base_url="https://localhost")
        self.responses.append(head)
        self.get("/" + name, headers={"Range": "bytes=0-2"})
        self.get("/" + name, headers={"If-None-Match": first.headers["ETag"]})
        with self.app.app_context():
            self.assertEqual(state.file_record(name)["hits"], 1)
        other = create_app(self.config)
        with other.app_context():
            self.assertEqual(state.file_record(name)["hits"], 1)

    def test_library_escapes_names_and_filters_and_paginates(self):
        self.login()
        with self.app.app_context(), state.transaction() as connection:
            connection.executemany(
                "INSERT INTO uploaded_names (filename, backed_up, original_name, created) VALUES (?, 0, ?, ?)",
                [(f"file-{index:02d}.txt", "<script>alert(1)</script>" if index == 0 else "original", index) for index in range(30)],
            )
        response = self.get("/panel/files?sort=oldest")
        self.assertIn("&lt;script&gt;", response.text)
        self.assertNotIn("<script>alert(1)</script>", response.text)
        self.assertIn("Page 1 of 2", response.text)
        self.assertNotIn("file-29.txt", response.text)
        self.assertIn("file-29.txt", self.get("/panel/files?page=2&sort=oldest").text)
        self.assertIn("Files <span class=\"muted\">1</span>", self.get("/panel/files?q=file-29").text)

    def test_bulk_delete_requires_confirmation(self):
        name = self.stored_name(self.upload())
        self.login()
        token = self.csrf("/panel/files")
        response = self.post("/panel/files/bulk", data={"csrf_token": token, "filename": name, "action": "delete"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Permanently delete files?", response.text)
        download = self.get("/" + name)
        self.assertEqual(download.status_code, 200)
        download.close()
        response = self.post("/panel/files/bulk", data={"csrf_token": token, "filename": name, "action": "delete", "confirmation": "delete"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.get("/" + name).status_code, 404)

    def test_panel_multi_upload_results_and_redirect_after_post(self):
        self.login()
        token = self.csrf("/panel/upload")
        response = self.post("/panel/file-upload", data={
            "csrf_token": token, "fileupload": [(io.BytesIO(b"one"), "one.txt"), (io.BytesIO(b"two"), "two.txt")],
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 303)
        result = self.get(response.headers["Location"])
        self.assertIn("Copy link", result.text)
        self.assertEqual(result.text.count("data-copy="), 2)

    def test_local_only_clear_requires_second_confirmation(self):
        name = self.stored_name(self.upload())
        self.login()
        token = self.csrf("/panel/files")
        response = self.post("/panel/cache/clear", data={"csrf_token": token})
        self.assertEqual(response.status_code, 200)
        self.assertIn(name, response.text)
        self.assertTrue((self.root / "cache" / name).exists())

    def test_failed_local_deletion_disables_link_before_cleanup(self):
        name = self.stored_name(self.upload())
        self.login()
        token = self.csrf("/panel/files")
        target = Mock()
        target.unlink.side_effect = PermissionError("file is open")
        with patch("core.cache.cache_filepath", return_value=target):
            response = self.post("/panel/files/delete/" + name, data={"csrf_token": token})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.get("/" + name).status_code, 404)
        self.assertTrue((self.root / "cache" / name).exists())
        with self.app.app_context():
            cache.cleanup_deleted()
        self.assertFalse((self.root / "cache" / name).exists())

    def test_partial_b2_deletion_is_blocked_and_retryable(self):
        bucket = Mock()
        bucket.list_file_versions.return_value = [SimpleNamespace(id_="version")]
        bucket.delete_file_version.side_effect = RuntimeError("offline")
        self.enable_b2(bucket)
        with self.app.app_context():
            b2connect.remember_remote(b2connect.RemoteFile("remote.txt", 5, {}, time.time()))
        self.login()
        token = self.csrf("/panel/files")
        self.assertEqual(self.post("/panel/files/delete/remote.txt", data={"csrf_token": token}).status_code, 503)
        self.assertEqual(self.get("/remote.txt").status_code, 404)
        self.assertIn("Deletion pending", self.get("/panel/files").text)
        bucket.delete_file_version.side_effect = None
        self.assertEqual(self.post("/panel/files/delete/remote.txt", data={"csrf_token": token}).status_code, 303)
        self.assertEqual(self.get("/remote.txt").status_code, 404)

    def test_small_remote_download_is_cached(self):
        bucket = Mock()
        bucket.get_file_info_by_name.return_value = SimpleNamespace(content_length=5, file_info={}, upload_timestamp=1700000000000)
        response = Mock()
        response.iter_content.return_value = [b"hello"]
        bucket.download_file_by_name.return_value = SimpleNamespace(response=response)
        self.enable_b2(bucket)
        self.assertEqual(self.get("/remote.txt").data, b"hello")
        self.assertEqual((self.root / "cache" / "remote.txt").read_bytes(), b"hello")
        self.assertEqual(self.get("/remote.txt").data, b"hello")
        self.assertEqual(bucket.download_file_by_name.call_count, 1)

    def test_remote_download_streams_when_local_storage_is_full(self):
        bucket = Mock()
        bucket.get_file_info_by_name.return_value = SimpleNamespace(content_length=5, file_info={}, upload_timestamp=1700000000000)
        stream = Mock()
        stream.headers = {"Content-Length": "5"}
        stream.iter_content.side_effect = lambda *args: iter([b"hello"])
        bucket.download_file_by_name.return_value = SimpleNamespace(response=stream)
        self.enable_b2(bucket)
        with patch("core.b2connect.publish_cache", side_effect=AppError("Storage is full.", 507)):
            response = self.get("/remote.txt")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b"hello")
        self.assertFalse((self.root / "cache" / "remote.txt").exists())
        with self.app.app_context(), closing(state.connect("controls.sqlite3")) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM leases").fetchone()[0], 0)

    def test_remote_download_streams_when_staging_is_full(self):
        bucket = Mock()
        bucket.get_file_info_by_name.return_value = SimpleNamespace(content_length=5, file_info={}, upload_timestamp=1700000000000)
        stream = Mock()
        stream.headers = {"Content-Length": "5"}
        stream.iter_content.return_value = [b"hello"]
        bucket.download_file_by_name.return_value = SimpleNamespace(response=stream)
        self.enable_b2(bucket)
        with patch("core.b2connect.staged_file", side_effect=AppError("Temporary storage is busy.", 503)):
            response = self.get("/remote.txt")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b"hello")
        bucket.download_file_by_name.assert_called_once_with(file_name="remote.txt", range_=None)

    def test_remote_download_does_not_bypass_transfer_limit(self):
        bucket = Mock()
        bucket.get_file_info_by_name.return_value = SimpleNamespace(content_length=5, file_info={}, upload_timestamp=1700000000000)
        self.enable_b2(bucket)
        with self.app.app_context(), ExitStack() as stack:
            for _ in range(8):
                stack.enter_context(controls.lease("downloads", 8))
            self.assertEqual(self.get("/remote.txt").status_code, 503)
        bucket.download_file_by_name.assert_not_called()

    def test_incomplete_remote_download_is_not_retried_as_a_stream(self):
        bucket = Mock()
        bucket.get_file_info_by_name.return_value = SimpleNamespace(content_length=5, file_info={}, upload_timestamp=1700000000000)
        stream = Mock()
        stream.iter_content.return_value = [b"hi"]
        bucket.download_file_by_name.return_value = SimpleNamespace(response=stream)
        self.enable_b2(bucket)
        self.assertEqual(self.get("/remote.txt").status_code, 503)
        bucket.download_file_by_name.assert_called_once_with(file_name="remote.txt")

    def test_upload_auth_subrequest_and_form_key_rejection(self):
        self.assertEqual(self.get("/upload-auth").status_code, 401)
        self.assertEqual(self.get("/upload-auth", headers={"X-Upload-Key": self.upload_key}).status_code, 204)
        self.assertEqual(self.post("/upload", data={"key": self.upload_key, "fileupload": (io.BytesIO(b"hello"), "fixture.txt")}).status_code, 401)
        with self.assertRaises(RuntimeError):
            Settings.load(self.config | {"LEGACY_UPLOAD_MAX_MB": 1})

    def test_image_pixel_limit_is_enforced(self):
        self.app.extensions["settings"] = replace(self.app.extensions["settings"], image_max_pixels=100)
        original = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(original, format="PNG")
        self.assertEqual(self.upload(original.getvalue(), "fixture.png").status_code, 422)

    def test_global_rate_bucket_survives_cache_pressure(self):
        with self.app.app_context():
            controls.rate_limit("test-global", "all", 1)
            with state.transaction("controls.sqlite3") as connection:
                connection.executemany("INSERT INTO limits VALUES (?, 1, ?)", [(str(index), time.time() + 60) for index in range(4100)])
            controls.rate_limit("other", "new-ip", 10)
            with self.assertRaises(AppError) as caught:
                controls.rate_limit("test-global", "all", 1)
            self.assertEqual(caught.exception.status, 429)

    def test_health_and_startup_do_not_contact_b2(self):
        bucket = Mock()
        self.enable_b2(bucket)
        self.assertEqual(self.get("/health").status_code, 200)
        self.assertFalse(bucket.mock_calls)

    def test_form_keys_and_query_keys_cannot_start_uploads(self):
        for path in ("/upload", "/upload?key=" + self.upload_key):
            response, count = self.probe_upload_body(path, fields={"key": self.upload_key})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(count, 0)
        self.assert_no_upload_started()

    def test_conflicting_and_malformed_auth_headers_do_not_read_body(self):
        cases = [
            {"Authorization": "Bearer " + self.upload_key, "X-Upload-Key": self.upload_key},
            {"Authorization": "Basic " + self.upload_key},
            {"Authorization": "Bearer "},
        ]
        for headers in cases:
            response, count = self.probe_upload_body("/upload", headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(count, 0)
        self.assert_no_upload_started()

    def test_panel_upload_requires_csrf_header_before_body(self):
        self.login()
        token = self.csrf("/panel/upload")
        for headers in ({}, {"X-CSRF-Token": "wrong"}, {"X-CSRF-Token": "malformed-é"}):
            response, count = self.probe_upload_body("/panel/file-upload", headers=headers, fields={"csrf_token": token})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(count, 0)
        self.assert_no_upload_started()

    def test_revoked_session_and_revoked_key_do_not_read_upload_body(self):
        self.login()
        token = self.csrf("/panel/upload")
        with self.app.app_context(), state.transaction() as connection:
            connection.execute("DELETE FROM sessions")
        response, count = self.probe_upload_body("/panel/file-upload", headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(count, 0)
        with self.app.app_context():
            key = create_upload_key("Revoked laptop")
            with state.transaction() as connection:
                connection.execute("DELETE FROM upload_keys")
        response, count = self.probe_upload_body("/upload", headers={"X-Upload-Key": key})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(count, 0)
        self.assert_no_upload_started()

    def test_non_upload_endpoints_reject_multipart_without_reading_body(self):
        for path in ("/panel/login", "/panel/keys/create", "/health", "/unknown"):
            response, count = self.probe_upload_body(path)
            self.assertEqual(response.status_code, 415)
            self.assertEqual(count, 0)
        self.assert_no_upload_started()

    def test_panel_preflight_requires_session_and_csrf_header(self):
        self.assertEqual(self.get("/panel/upload-auth").status_code, 401)
        self.login()
        token = self.csrf("/panel/upload")
        self.assertEqual(self.get("/panel/upload-auth").status_code, 403)
        self.assertEqual(self.get("/panel/upload-auth", headers={"X-CSRF-Token": token}).status_code, 204)
        self.assert_no_upload_started()

    def test_credential_upload_limit_cannot_be_bypassed_by_rotating_ip(self):
        for index in range(5):
            response = self.post("/upload", headers={"X-Upload-Key": self.upload_key},
                                 data={"fileupload": (io.BytesIO(b"hello"), "fixture.txt")},
                                 environ_overrides={"REMOTE_ADDR": f"10.0.0.{index + 1}"})
            self.assertEqual(response.status_code, 200)
        response, count = self.probe_upload_body("/upload", headers={"X-Upload-Key": self.upload_key}, address="10.0.0.99")
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["Retry-After"]), 0)
        self.assertEqual(count, 0)

    def test_batch_files_and_api_share_upload_quota(self):
        for _ in range(3):
            self.stored_name(self.upload())
        self.login()
        token = self.csrf("/panel/upload")
        response = self.post("/panel/file-upload", headers={"X-CSRF-Token": token, "X-Requested-With": "XMLHttpRequest"},
                             data={"csrf_token": token, "fileupload": [(io.BytesIO(b"hello"), f"batch-{index}.txt") for index in range(3)]})
        self.assertEqual(response.status_code, 207)
        self.assertEqual(len(response.json["files"]), 2)
        self.assertEqual(response.json["errors"][0]["status"], 429)
        with self.app.app_context(), closing(state.connect()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM uploaded_names WHERE status = 'available'").fetchone()[0], 5)

    def test_global_upload_limit_spans_credentials_and_ips(self):
        with self.app.app_context():
            keys = [create_upload_key(f"Device {index}") for index in range(5)]
        for index, key in enumerate(keys[:4]):
            for _ in range(5):
                response = self.post("/upload", headers={"X-Upload-Key": key},
                                     data={"fileupload": (io.BytesIO(b"hello"), "fixture.txt")},
                                     environ_overrides={"REMOTE_ADDR": f"10.0.0.{index + 1}"})
                self.assertEqual(response.status_code, 200)
        response, count = self.probe_upload_body("/upload", headers={"X-Upload-Key": keys[4]}, address="10.0.0.99")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(count, 0)

    def test_upload_limits_use_rolling_windows(self):
        with self.app.test_request_context("/upload", method="POST", base_url="https://localhost"):
            g.upload_authorized, g.upload_principal = True, "key:unit-test"
            with patch("core.uploads.time.time", return_value=1700000039):
                for _ in range(5):
                    reserve_upload_attempt()
            with patch("core.uploads.time.time", return_value=1700000041), self.assertRaises(AppError) as caught:
                reserve_upload_attempt()
            self.assertEqual(caught.exception.status, 429)
            with patch("core.uploads.time.time", return_value=1700000100):
                reserve_upload_attempt()

    def test_hourly_credential_limit_is_enforced(self):
        with self.app.test_request_context("/upload", method="POST", base_url="https://localhost"):
            g.upload_authorized, g.upload_principal = True, "key:unit-test"
            for batch in range(12):
                with patch("core.uploads.time.time", return_value=1700000000 + batch * 61):
                    for _ in range(5):
                        reserve_upload_attempt()
            with patch("core.uploads.time.time", return_value=1700000732), self.assertRaises(AppError) as caught:
                reserve_upload_attempt()
            self.assertEqual(caught.exception.status, 429)
            self.assertGreater(caught.exception.retry_after, 60)

    def test_two_request_limit_rejects_before_body_read(self):
        with self.app.app_context(), ExitStack() as stack:
            for _ in range(2):
                stack.enter_context(controls.lease("upload-requests", 2))
            response, count = self.probe_upload_body("/upload", headers={"X-Upload-Key": self.upload_key})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(count, 0)

    def test_legacy_deleted_records_are_not_resurrected(self):
        with self.app.app_context(), state.transaction() as connection:
            connection.execute("INSERT INTO uploaded_names (filename, backed_up, deleted) VALUES ('legacy.txt', 0, 1)")
        (self.root / "cache" / "legacy.txt").write_bytes(b"old")
        other = create_app(self.config)
        with other.app_context():
            self.assertTrue(state.is_deleted("legacy.txt"))


class FlameshotTests(unittest.TestCase):
    def test_client_rejects_a_successful_error_message(self):
        path = Path(__file__).resolve().parents[2] / "flameshot-addon" / "flameshot-bsxu.py"
        spec = importlib.util.spec_from_file_location("flameshot_client", path)
        client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(client)
        response = Mock()
        response.status_code, response.text = 200, "Invalid upload key."
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(client.requests, "post", return_value=response) as post:
            with self.assertRaises(ValueError):
                client.upload(b"fixture", "https://files.example.com/upload", "u" * 40)
            self.assertEqual(post.call_args.kwargs["timeout"], (5, 120))
            self.assertFalse(post.call_args.kwargs["allow_redirects"])


if __name__ == "__main__":
    unittest.main()

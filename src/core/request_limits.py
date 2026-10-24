from contextlib import contextmanager
import tempfile
import time

from flask import Request, g, request

from core.cache import staged_file
from core.controls import lease
from core.errors import AppError
from core.uploads import UPLOAD_CONCURRENCY, ensure_upload_authorized


class DeadlineInput:
    def __init__(self, stream):
        self.stream = stream
        self.deadline = time.monotonic() + 120

    def check(self):
        if time.monotonic() > self.deadline:
            raise AppError("Upload body timed out. Please retry.", 408)

    def read(self, *args):
        self.check()
        return self.stream.read(*args)

    def readinto(self, buffer):
        self.check()
        return self.stream.readinto(buffer)

    def readline(self, *args):
        self.check()
        return self.stream.readline(*args)

    def close(self):
        self.stream.close()


class UploadRequest(Request):
    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        directory = g.get("upload_spool")
        ensure_upload_authorized()
        if directory is None:
            raise AppError("Upload authorization is required before accepting file data.", 401)
        stream = tempfile.TemporaryFile(mode="w+b", dir=directory)
        self.__dict__.setdefault("upload_streams", []).append(stream)
        return stream


@contextmanager
def upload_request():
    ensure_upload_authorized()
    with lease("upload-requests", UPLOAD_CONCURRENCY), staged_file(request.max_content_length) as temporary:
        g.upload_spool = temporary.parent
        request.environ["wsgi.input"] = DeadlineInput(request.environ["wsgi.input"])
        try:
            yield
        finally:
            request.close()
            for stream in request.__dict__.get("upload_streams", []):
                stream.close()
            g.pop("upload_spool", None)

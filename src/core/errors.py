class AppError(Exception):
    def __init__(self, message, status=400, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class BackendError(AppError):
    def __init__(self, message="Storage is temporarily unavailable. Please retry."):
        super().__init__(message, 503)


class NotFound(AppError):
    def __init__(self):
        super().__init__("File does not exist or its link has expired.", 404)

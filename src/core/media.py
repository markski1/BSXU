from pathlib import Path
import subprocess
import sys

from core.config import settings
from core.controls import lease
from core.errors import AppError

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".ico"}
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".webm", ".ogv"}
MIMETYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".ico": "image/vnd.microsoft.icon", ".avif": "image/avif",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg",
}


def process_image(source, extension):
    config = settings()
    destination = source.with_name("cleaned.part")
    thumbnail = source.with_name("thumbnail.png")
    with lease("images", 2):
        try:
            result = subprocess.run(
                [sys.executable, "-I", str(Path(__file__).with_name("media_worker.py")),
                 str(source), str(destination), str(thumbnail), extension,
                 str(config.image_max_pixels), str(config.image_max_frames),
                 str(config.max_upload_size_mb * 1024 * 1024), str(config.image_timeout)],
                timeout=config.image_timeout + 2, capture_output=True, check=False,
            )
        except subprocess.TimeoutExpired as exception:
            raise AppError("Image processing timed out. Use a smaller image.", 422) from exception
    if result.returncode or not destination.is_file() or not thumbnail.is_file():
        raise AppError("Image could not be safely processed. Check its format, size and frame count.", 422)
    return destination, thumbnail

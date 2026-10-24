import json
import sys
import warnings
from pathlib import Path

from PIL import Image, ImageOps

FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".gif": "GIF",
           ".webp": "WEBP", ".avif": "AVIF", ".bmp": "BMP", ".ico": "ICO"}


def clean(source, destination, thumbnail, extension, max_pixels, max_frames, max_bytes):
    Image.MAX_IMAGE_PIXELS = max_pixels
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    with Image.open(source, formats=[FORMATS[extension]]) as image:
        count = getattr(image, "n_frames", 1)
        if count > max_frames:
            raise ValueError("Image contains too many frames.")
        frames, durations, pixels = [], [], 0
        loop = image.info.get("loop", 0)
        for index in range(count):
            image.seek(index)
            width, height = image.size
            pixels += width * height
            if width * height > max_pixels or pixels > max_pixels * 2:
                raise ValueError("Decoded image exceeds the pixel limit.")
            duration = int(image.info.get("duration", 100))
            frame = ImageOps.exif_transpose(image).convert("RGBA")
            frame.info.clear()
            frames.append(frame)
            durations.append(max(1, duration))
        first = frames[0]
        options = {}
        if len(frames) > 1:
            if FORMATS[extension] not in {"GIF", "PNG", "WEBP", "AVIF"}:
                raise ValueError("Animated format is not supported.")
            options.update(save_all=True, append_images=frames[1:], duration=durations, loop=loop)
            if extension == ".gif":
                options["disposal"] = 2
        if FORMATS[extension] == "JPEG":
            first = first.convert("RGB")
            options.update(quality=90, exif=b"")
        first.save(destination, format=FORMATS[extension], **options)
        if Path(destination).stat().st_size > max_bytes:
            raise ValueError("Cleaned image exceeds the upload limit.")
        preview = frames[0].copy()
        preview.thumbnail((192, 192))
        preview.info.clear()
        preview.save(thumbnail, format="PNG")


if __name__ == "__main__":
    try:
        source, destination, thumbnail, extension = sys.argv[1:5]
        max_pixels, max_frames, max_bytes, timeout = map(int, sys.argv[5:9])
        if sys.platform != "win32":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout))
            resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes, max_bytes))
        clean(source, destination, thumbnail, extension, max_pixels, max_frames, max_bytes)
    except Exception:
        print(json.dumps({"error": "Image could not be safely processed. Check its format, size and frame count."}))
        sys.exit(1)

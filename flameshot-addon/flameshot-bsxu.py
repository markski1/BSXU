import io
import os
import sys
from urllib.parse import urlsplit

import requests


def upload(data, url, key):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise ValueError("BSXU_UPLOAD_URL must be a clean HTTPS upload URL.")
    if len(key) < 32:
        raise ValueError("Set BSXU_UPLOAD_KEY to an upload-only key.")
    if not data or len(data) > 128 * 1024 * 1024:
        raise ValueError("Screenshot is empty or exceeds the client upload limit.")
    with requests.post(
        url, headers={"Authorization": "Bearer " + key},
        files={"fileupload": ("screenshot.png", io.BytesIO(data), "image/png")},
        timeout=(5, 120), allow_redirects=False,
    ) as response:
        response.raise_for_status()
        link = response.text.strip()
        target = urlsplit(link)
        if response.status_code not in {200, 201} or target.scheme != "https" or target.netloc != parsed.netloc or target.query or target.fragment or target.username or not target.path.strip("/"):
            raise ValueError("Server did not return a valid file URL.")
        return link


def main():
    try:
        link = upload(sys.stdin.buffer.read(128 * 1024 * 1024 + 1),
                      os.environ.get("BSXU_UPLOAD_URL", ""),
                      os.environ.get("BSXU_UPLOAD_KEY", ""))
        import pyperclip
        pyperclip.copy(link)
        return 0
    except (requests.RequestException, ValueError) as exception:
        print("Upload failed: " + str(exception), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

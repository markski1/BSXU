# Based on Flameshot-Custom-Uploader by CanCodes
# https://github.com/CanCodes/Flameshot-Custom-Uploader/blob/master/local/main.py

import io
from requests import post
import sys
import pyperclip
from PIL import Image

config = {
    'url': "https://files.example.com/upload",
    'key': "BSXU_AUTH_KEY"
}

if __name__ == "__main__":
    data = sys.stdin.buffer.read()
    im = Image.open(io.BytesIO(data))
    r = post(
        config['url'],
        data={'key': config['key']},
        files={'fileupload': io.BytesIO(data)}
    )
    if r.status_code == 200:
        pyperclip.copy(r.text)
    else:
        print("error, check BSXU's console.")

import string
import random
import traceback

import requests

from core.config import webhook_url, using_webhook


def generate_random_string(length):
    length = int(length)
    alphanumeric_characters = string.ascii_letters + string.digits
    return ''.join(random.choice(alphanumeric_characters) for _ in range(length))


def wh_report(text, exception=None):
    message = "[BSXU] - " + text
    if exception:
        try:
            exception_text = f"{exception} \n ```{traceback.format_tb(exception.__traceback__)}```"
        except:
            exception_text = f"Another exception was met trying to get a traceback for the original exception. \n ({exception})"
    else:
        exception_text = ""

    if using_webhook:
        requests.post(webhook_url, json={
            'content': message
        })
        if exception:
            requests.post(webhook_url, json={
                'content': exception_text
            })

    print(message)
    if exception:
        print(exception_text)

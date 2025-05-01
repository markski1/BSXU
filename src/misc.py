import string
import random
import traceback
import os
import requests
from dotenv import load_dotenv

load_dotenv()

webhook_url = os.getenv('WEBHOOK_URL', 'false')
using_webhook = webhook_url.lower() != "false"
webhook_param = os.getenv('WEBHOOK_PARAM', 'content')


def generate_random_string(length: int) -> str:
    length = int(length)
    alphanumeric_characters = string.ascii_letters + string.digits
    return ''.join(random.choice(alphanumeric_characters) for _ in range(length))


def wh_report(text: str, exception: Exception = None) -> None:
    message = "[BSXU] - " + text
    if exception:
        try:
            exception_text = f"{exception} \n ```{traceback.format_tb(exception.__traceback__)}```"
        except:
            exception_text = (f"Another exception was met trying to get a traceback for the original exception. "
                              f"\n ({exception})")
    else:
        exception_text = ""

    if using_webhook:
        requests.post(webhook_url, json={
            webhook_param: message
        })
        if exception:
            requests.post(webhook_url, json={
                webhook_param: exception_text
            })

    print(message)
    if exception:
        print(exception_text)

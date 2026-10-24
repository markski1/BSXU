import logging
from queue import Full, Queue
import secrets
import string
import threading

import requests
from flask import current_app, has_app_context

notifications = Queue(maxsize=20)
worker_lock = threading.Lock()
worker_started = False


def generate_random_string(length):
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))


def send_notifications():
    while True:
        url, parameter, message = notifications.get()
        try:
            with requests.post(url, json={parameter: message}, timeout=(3, 5)) as response:
                response.raise_for_status()
        except requests.RequestException:
            logging.getLogger("bsxu").warning("Webhook notification failed.")
        finally:
            notifications.task_done()


def wh_report(text, exception=None):
    global worker_started
    logger = current_app.logger if has_app_context() else logging.getLogger("bsxu")
    if exception is not None:
        logger.error(text, exc_info=(type(exception), exception, exception.__traceback__))
    else:
        logger.info(text)
    if not has_app_context():
        return
    config = current_app.extensions["settings"]
    if config.webhook_url == "false":
        return
    with worker_lock:
        if not worker_started:
            threading.Thread(target=send_notifications, daemon=True, name="bsxu-webhooks").start()
            worker_started = True
    try:
        notifications.put_nowait((config.webhook_url, config.webhook_param, "[BSXU] " + text))
    except Full:
        logger.warning("Webhook queue is full.")

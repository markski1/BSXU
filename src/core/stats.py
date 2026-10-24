import time

from core.state import transaction


def count_hit(filename):
    with transaction() as connection:
        connection.execute("UPDATE uploaded_names SET hits = hits + 1, accessed = ? WHERE filename = ?", (time.time(), filename))

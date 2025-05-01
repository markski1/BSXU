from datetime import datetime

file_hits = {}
start_date = datetime.now()


def count_hit(filename: str) -> None:
    global file_hits
    if filename in file_hits:
        file_hits[filename] += 1

    else:
        file_hits[filename] = 1


def get_total_hits() -> int:
    return sum(file_hits.values())


def get_file_hits(filename: str) -> int:
    return file_hits.get(filename, 0)


def get_all_file_hits() -> int:
    return file_hits

import string
from random import random


def generate_random_string(length):
    characters = string.ascii_letters + string.digits
    gen_str = ''.join(random.choice(characters) for _ in range(length))
    return gen_str

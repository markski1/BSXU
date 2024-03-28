import os
from flask import Flask, Response
from dotenv import load_dotenv

load_dotenv()

authkey = os.getenv('API_KEY')
url_path = os.getenv('URL_PATH')
app = Flask(__name__)


@app.route("/")
def index():
    return 'ok'


@app.route("/upload")
def upload_file():
    # TODO: Check auth key, upload file

    return "ok"


@app.route("/<string:filename>")
def get_file(filename):
    # TODO: Fetch file, serve

    return "ok"


if __name__ == "__main__":
    app.run(host=os.getenv('APP_HOST'), port=os.getenv('APP_PORT'), debug=os.getenv('APP_DEBUG'))

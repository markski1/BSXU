from flask import render_template
from core.config import app


@app.route("/")
def index():
    return render_template("login.html")

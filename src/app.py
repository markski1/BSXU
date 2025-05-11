from core.config import app, app_host, app_port, app_debug, panel_enabled

from misc import wh_report

from routes.panel import panel_bp
from routes.file import upload_file, get_file, get_file_thumbnail


@app.route("/")
def index():
    return "<h1>It works!</h1>"


# Register root routes.
app.add_url_rule("/upload", "upload_file", upload_file, methods=["POST"])
app.add_url_rule("/<string:filename>", "get_file", get_file)
app.add_url_rule("/thumbnail/<string:filename>", "get_file_thumbnail", get_file_thumbnail)


# If the panel is enabled in the envfile, register its blueprint.
if panel_enabled:
    app.register_blueprint(panel_bp)

wh_report("BSXU is starting.")

if __name__ == "__main__":
    app.run(host=app_host, port=app_port, debug=app_debug)

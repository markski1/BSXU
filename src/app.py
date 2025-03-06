from core.config import app, app_host, app_port, app_debug, panel_enabled

from misc import wh_report

from routes.panel import panel_bp
from routes.file import upload_file, get_file, get_cache_file


@app.route("/")
def index():
    return "BSXU is running."


# Register root routes.
app.add_url_rule("/upload", "upload_file", upload_file, methods=["POST"])
app.add_url_rule("/<string:filename>", "get_file", get_file)
app.add_url_rule("/cache/<string:filename>", "get_cache_file", get_cache_file)


# If the panel is enabled in the envfile, register its blueprint.
if panel_enabled:
    app.register_blueprint(panel_bp)

wh_report("BSXU is starting.")

if __name__ == "__main__":
    app.run(host=app_host, port=app_port, debug=app_debug)

from core.config import app, app_host, app_port, app_debug, panel_enabled

# Routes, "unused" but needs to be imported for routes to load.
import routes.file
from misc import wh_report

from routes.panel import panel_bp

if panel_enabled:
    app.register_blueprint(panel_bp)


@app.route("/")
def index():
    return "BSXU is running."


if __name__ == "__main__":
    wh_report("BSXU is starting.")
    app.run(host=app_host, port=app_port, debug=app_debug)

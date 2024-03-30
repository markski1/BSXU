from core.config import app, app_host, app_port, app_debug

# Routes
import routes.file
import routes.interface


if __name__ == "__main__":
    app.run(host=app_host, port=app_port, debug=app_debug)

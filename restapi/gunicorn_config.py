# Gunicorn WSGI config for MANTIS API
# Run: gunicorn -c gunicorn_config.py main:app

workers = int(__import__("os").environ.get("gunicorn_workers", 2))
timeout = int(__import__("os").environ.get("gunicorn_timeout", 120))
bind = __import__("os").environ.get(
    "gunicorn_bind",
    f"0.0.0.0:{__import__('os').environ.get('py_flask_port', '8080')}",
)
loglevel = __import__("os").environ.get("gunicorn_loglevel", "info")
accesslog = "-"
errorlog = "-"

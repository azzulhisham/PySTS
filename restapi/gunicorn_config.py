# Gunicorn WSGI config for MANTIS API
# Run: gunicorn -c gunicorn_config.py main:app
# Default timeout 300s so /mantis/vessel-track can stream up to 3 days of 20-minute chunks.

workers = int(__import__("os").environ.get("gunicorn_workers", 2))
timeout = int(__import__("os").environ.get("gunicorn_timeout", 300))
bind = __import__("os").environ.get(
    "gunicorn_bind",
    f"0.0.0.0:{__import__('os').environ.get('py_flask_port', '8080')}",
)
loglevel = __import__("os").environ.get("gunicorn_loglevel", "info")
accesslog = "-"
errorlog = "-"

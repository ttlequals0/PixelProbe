"""Gunicorn configuration.

Defaults match the previous hardcoded Dockerfile CMD, so existing deployments
behave as before. GUNICORN_BIND accepts a comma-separated list of addresses;
see docs/configuration.md for the dual-stack (IPv6) guidance and caveats.
"""

import os


def _env_int(name, default):
    # Blank values (e.g. compose passthrough of an unset var) and garbage
    # fall back to the default instead of crash-looping the container.
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


_env_bind = os.environ.get("GUNICORN_BIND", "")
bind = [b.strip() for b in _env_bind.split(",") if b.strip()] or ["0.0.0.0:5000"]
workers = _env_int("GUNICORN_WORKERS", 4)
timeout = _env_int("GUNICORN_TIMEOUT", 300)
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL") or "info"

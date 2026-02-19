"""Rate limiting configuration for the application"""
from flask import Flask, current_app
from flask_limiter import Limiter
from functools import wraps
import logging

logger = logging.getLogger(__name__)


def rate_limit(limit_string):
    """Decorator to apply rate limits using the app's limiter.

    Args:
        limit_string: Rate limit string (e.g., "10 per minute", "100 per hour")
    """
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            limiter = current_app.extensions.get('flask-limiter')
            if limiter:
                limited_func = limiter.limit(limit_string, exempt_when=lambda: False)(f)
                return limited_func(*args, **kwargs)
            else:
                return f(*args, **kwargs)
        return wrapped
    return decorator


def exempt_from_rate_limit(f):
    """Decorator to exempt a function from rate limiting"""
    @wraps(f)
    def wrapped(*args, **kwargs):
        limiter = current_app.extensions.get('flask-limiter')
        if limiter:
            exempt_func = limiter.exempt(f)
            return exempt_func(*args, **kwargs)
        else:
            return f(*args, **kwargs)
    return wrapped


def apply_rate_limits(app: Flask, limiter: Limiter):
    """Apply rate limits to blueprint endpoints after registration"""
    try:
        # Get blueprints
        scan_bp = app.blueprints.get('scan')
        admin_bp = app.blueprints.get('admin')
        maintenance_bp = app.blueprints.get('maintenance')
        auth_bp = app.blueprints.get('auth')

        if scan_bp:
            # Apply rate limits to scan endpoints
            if 'scan.scan_file' in app.view_functions:
                limiter.limit("5 per minute")(app.view_functions['scan.scan_file'])
            if 'scan.scan_all' in app.view_functions:
                limiter.limit("2 per minute")(app.view_functions['scan.scan_all'])
            if 'scan.scan_parallel' in app.view_functions:
                limiter.limit("2 per minute")(app.view_functions['scan.scan_parallel'])

        if admin_bp:
            # Apply rate limits to admin endpoints
            if 'admin.cleanup_files' in app.view_functions:
                limiter.limit("10 per minute")(app.view_functions['admin.cleanup_files'])
            if 'admin.mark_as_good' in app.view_functions:
                limiter.limit("10 per minute")(app.view_functions['admin.mark_as_good'])

        if maintenance_bp:
            # Apply rate limits to maintenance endpoints
            if 'maintenance.vacuum_database' in app.view_functions:
                limiter.limit("5 per minute")(app.view_functions['maintenance.vacuum_database'])

        if auth_bp:
            # Apply rate limits to authentication endpoints (P1 security hardening)
            if 'auth.api_login' in app.view_functions:
                limiter.limit("5 per minute")(app.view_functions['auth.api_login'])
            if 'auth.first_run_setup' in app.view_functions:
                limiter.limit("3 per hour")(app.view_functions['auth.first_run_setup'])

        logger.info("Rate limits applied successfully")
    except Exception as e:
        logger.error(f"Error applying rate limits: {e}")
        # Don't fail the app startup if rate limiting setup fails

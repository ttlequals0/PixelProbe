"""
Decorators for PixelProbe routes
"""

import logging
from functools import wraps

from flask import jsonify, request

logger = logging.getLogger(__name__)


def require_json(f):
    """Decorator to ensure request has JSON content type"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method in ['POST', 'PUT', 'PATCH'] and not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400
        return f(*args, **kwargs)
    return decorated_function


def handle_errors(f):
    """Decorator to handle exceptions in routes.

    Logs the exception server-side with full traceback but returns a generic
    error response to the client. Do not expose exception details over HTTP.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception:
            logger.error("Error in %s", f.__name__, exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    return decorated_function
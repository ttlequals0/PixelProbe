"""
Decorators for PixelProbe routes
"""

from functools import wraps
from flask import request, jsonify
import logging
import os

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
    """Decorator to handle exceptions in routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}", exc_info=True)
            return jsonify({'error': 'Internal server error', 'message': str(e)}), 500
    return decorated_function

def validate_path_exists(f):
    """Decorator to validate that a path exists"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        data = request.get_json() or {}
        path = data.get('path') or data.get('file_path')
        
        if path and not os.path.exists(path):
            return jsonify({'error': f'Path does not exist: {path}'}), 404
            
        return f(*args, **kwargs)
    return decorated_function
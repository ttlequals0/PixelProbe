"""
Authentication decorator wrapper for API routes
Applies authentication requirement to all API endpoints
"""

from functools import wraps
from auth import auth_required

def apply_auth_to_blueprint(blueprint):
    """
    Apply authentication requirement to all routes in a blueprint
    This allows API routes to work with both cookies and API tokens
    """
    for endpoint, view_func in blueprint.view_functions.items():
        # Skip if already has authentication
        if not hasattr(view_func, '_auth_applied'):
            wrapped = auth_required(view_func)
            wrapped._auth_applied = True
            blueprint.view_functions[endpoint] = wrapped
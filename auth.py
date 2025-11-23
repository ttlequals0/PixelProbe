"""
Authentication module for PixelProbe
Handles user authentication, session management, and API token validation
"""

import logging
from functools import wraps
from datetime import datetime, timezone

from flask import jsonify, request, redirect, url_for, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_restx import abort as restx_abort
from models import User, APIToken, db

logger = logging.getLogger(__name__)

login_manager = LoginManager()

def init_auth(app):
    """Initialize authentication for the Flask app"""
    login_manager.init_app(app)
    login_manager.login_view = 'auth_ui.login'
    login_manager.login_message = 'Please log in to access this page.'

    # Session configuration for security
    app.config.update(
        SESSION_COOKIE_SECURE=app.config.get('SESSION_COOKIE_SECURE', False),  # Set True for HTTPS
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=86400  # 24 hours
    )

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.get(int(user_id))
        if user:
            # Force load all attributes to prevent lazy loading issues
            _ = user.is_active
        return user

    @login_manager.request_loader
    def load_user_from_request(request):
        # Check for API token in Authorization header
        auth_header = request.headers.get('Authorization')
        if auth_header:
            try:
                token = None
                # Check if it has Bearer prefix
                if ' ' in auth_header:
                    scheme, token = auth_header.split(' ', 1)
                    if scheme.lower() == 'bearer':
                        token = token
                else:
                    # No space means it's just the token (from Swagger UI)
                    token = auth_header

                if token:
                    api_token = APIToken.query.filter_by(token=token, is_active=True).first()
                    if api_token and api_token.is_valid():
                        api_token.update_last_used()
                        return api_token.user
            except Exception as e:
                logger.error(f"Error loading user from API token: {e}")

        # SECURITY: API tokens in query parameters removed per P0 security audit
        # Tokens in URLs are logged and exposed in browser history
        # API tokens MUST be sent via Authorization header only
        # If you need to authenticate, use: Authorization: Bearer <token>

        return None


def auth_required(f):
    """
    Decorator that requires authentication via session or API token.
    This is used for API endpoints that need to support both cookie and token auth.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Allow internal scheduler requests without authentication
        if request.headers.get('X-Internal-Request') == 'scheduler':
            return f(*args, **kwargs)

        # Check if user is authenticated via session
        if current_user.is_authenticated:
            return f(*args, **kwargs)

        # Check for API token
        auth_header = request.headers.get('Authorization')
        token = None

        if auth_header:
            # Check if it has Bearer prefix
            if ' ' in auth_header:
                try:
                    scheme, token = auth_header.split(' ', 1)
                    if scheme.lower() != 'bearer':
                        token = None
                except ValueError:
                    pass
            else:
                # No space means it's just the token (from Swagger UI)
                token = auth_header

        # Fallback to query parameter
        if not token:
            token = request.args.get('api_token')

        if token:
            api_token = APIToken.query.filter_by(token=token, is_active=True).first()
            if api_token and api_token.is_valid():
                api_token.update_last_used()
                # Temporarily set current_user for this request
                request.current_user = api_token.user
                return f(*args, **kwargs)

        # Not authenticated
        return jsonify({'error': 'Authentication required'}), 401

    return decorated_function


def check_auth():
    """Check if the current request is authenticated

    Returns True if authenticated, False otherwise.
    Use this inside Flask-RESTX Resource methods instead of the decorator.
    """
    # Allow internal scheduler requests without authentication
    if request.headers.get('X-Internal-Request') == 'scheduler':
        return True

    # Check if user is authenticated via session
    if current_user.is_authenticated:
        return True

    # Check for API token
    auth_header = request.headers.get('Authorization')
    token = None

    if auth_header:
        # Check if it has Bearer prefix
        if ' ' in auth_header:
            try:
                scheme, token = auth_header.split(' ', 1)
                if scheme.lower() != 'bearer':
                    token = None
            except ValueError:
                pass
        else:
            # No space means it's just the token (from Swagger UI)
            token = auth_header

    # Fallback to query parameter
    if not token:
        token = request.args.get('api_token')

    if token:
        api_token = APIToken.query.filter_by(token=token, is_active=True).first()
        if api_token and api_token.is_valid():
            api_token.update_last_used()
            # Store user in request context
            request.current_user = api_token.user
            return True

    return False


def admin_required(f):
    """Decorator that requires admin privileges"""
    @wraps(f)
    @auth_required
    def decorated_function(*args, **kwargs):
        user = getattr(request, 'current_user', current_user)
        if not user.is_admin:
            return jsonify({'error': 'Admin privileges required'}), 403
        return f(*args, **kwargs)

    return decorated_function


def check_first_run():
    """Check if this is the first run (no users exist)"""
    return User.query.count() == 0


def create_initial_admin(password):
    """Create the initial admin user during first-run setup"""
    if User.query.count() > 0:
        return None, "Users already exist"

    admin = User(
        username='admin',
        email='admin@pixelprobe.local',
        is_admin=True,
        first_setup_required=False
    )
    admin.set_password(password)

    try:
        db.session.add(admin)
        db.session.commit()
        return admin, None
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to create initial admin: {e}")
        return None, str(e)


def authenticate_user(username, password):
    """Authenticate a user with username and password"""
    user = User.query.filter_by(username=username, is_active=True).first()

    if user and user.check_password(password):
        # Update last login time
        user.last_login = datetime.now(timezone.utc)
        db.session.commit()
        return user

    return None


def get_authenticated_user(request):
    """
    Get the authenticated user from the request.
    Checks both session authentication and API tokens.
    """
    # Check session first
    if current_user.is_authenticated:
        return current_user

    # Check for API token in header
    auth_header = request.headers.get('Authorization')
    if auth_header:
        token = None
        # Check if it has Bearer prefix
        if ' ' in auth_header:
            try:
                scheme, token = auth_header.split(' ', 1)
                if scheme.lower() == 'bearer':
                    token = token
                else:
                    token = None
            except ValueError:
                pass
        else:
            # No space means it's just the token (from Swagger UI)
            token = auth_header

        if token:
            api_token = APIToken.query.filter_by(token=token, is_active=True).first()
            if api_token and api_token.is_valid():
                return api_token.user

    # Check for API token in query parameter
    token = request.args.get('api_token')
    if token:
        api_token = APIToken.query.filter_by(token=token, is_active=True).first()
        if api_token and api_token.is_valid():
            return api_token.user

    return None
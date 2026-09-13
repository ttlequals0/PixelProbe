"""
Authentication module for PixelProbe
Handles user authentication, session management, and API token validation
"""

import hmac
import hashlib
import logging
import os
from functools import wraps
from datetime import datetime, timedelta, timezone

from flask import jsonify, request, redirect, url_for, session, current_app
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_restx import abort as restx_abort
from pixelprobe.models import User, APIToken, db

logger = logging.getLogger(__name__)

login_manager = LoginManager()


def _extract_bearer_token(req):
    """Extract a Bearer token from the Authorization header.

    Only accepts the standard ``Bearer <token>`` form.
    """
    auth_header = req.headers.get('Authorization')
    if not auth_header:
        return None

    if ' ' in auth_header:
        try:
            scheme, token = auth_header.split(' ', 1)
            if scheme.lower() == 'bearer':
                return token
        except ValueError:
            pass
        return None
    return None


def _lookup_api_token(token):
    if not token:
        return None
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    # Join the owner in the authentication query. Reading the relationship from
    # a scoped-session identity map can otherwise retain a pre-deactivation
    # User object for the duration of a worker request cycle.
    return (APIToken.query.join(User)
            .filter(APIToken.token_digest == digest,
                    APIToken.is_active.is_(True),
                    User.is_active.is_(True))
            .first())


def _valid_api_token(token):
    api_token = _lookup_api_token(token)
    if not api_token or not api_token.is_valid():
        return None
    return api_token


def request_uses_bearer_auth():
    """Return true only for an authenticated Bearer API-token request."""
    return _valid_api_token(_extract_bearer_token(request)) is not None


def request_uses_internal_auth():
    supplied = request.headers.get('X-Internal-Secret', '')
    expected = current_app.config.get('INTERNAL_API_SECRET', '')
    return bool(supplied and expected and hmac.compare_digest(supplied, expected))


def init_auth(app):
    """Initialize authentication for the Flask app"""
    login_manager.init_app(app)
    login_manager.login_view = 'auth_ui.login'
    login_manager.login_message = 'Please log in to access this page.'

    # Session configuration for security. Secure cookies default ON; plain-HTTP
    # LAN deployments must opt out with SESSION_COOKIE_SECURE=false.
    def env_bool(name, default):
        value = os.environ.get(name)
        if value is None:
            return app.config.get(name, default) if app.testing else default
        return value.lower() in {'1', 'true', 'yes', 'on'}

    cookie_secure = env_bool('SESSION_COOKIE_SECURE', True)
    cookie_httponly = env_bool('SESSION_COOKIE_HTTPONLY', True)
    cookie_samesite = os.environ.get('SESSION_COOKIE_SAMESITE')
    if cookie_samesite is None:
        configured_samesite = app.config.get('SESSION_COOKIE_SAMESITE')
        cookie_samesite = configured_samesite if isinstance(configured_samesite, str) else 'Lax'
    if cookie_samesite not in {'Lax', 'Strict', 'None'}:
        raise ValueError('SESSION_COOKIE_SAMESITE must be Lax, Strict, or None')
    remember_days = int(os.environ.get('REMEMBER_COOKIE_DURATION_DAYS',
                                      app.config.get('REMEMBER_COOKIE_DURATION_DAYS', 30)))
    if remember_days < 1 or remember_days > 365:
        raise ValueError('REMEMBER_COOKIE_DURATION_DAYS must be between 1 and 365')
    app.config.update(
        SESSION_COOKIE_SECURE=cookie_secure,
        SESSION_COOKIE_HTTPONLY=cookie_httponly,
        SESSION_COOKIE_SAMESITE=cookie_samesite,
        REMEMBER_COOKIE_SECURE=cookie_secure,
        REMEMBER_COOKIE_HTTPONLY=cookie_httponly,
        REMEMBER_COOKIE_SAMESITE=cookie_samesite,
        REMEMBER_COOKIE_DURATION=timedelta(days=remember_days),
        PERMANENT_SESSION_LIFETIME=timedelta(days=1)
    )

    # Session inactivity timeout (30 minutes) - P1 audit fix
    SESSION_INACTIVITY_TIMEOUT = 1800  # seconds

    @app.before_request
    def check_session_timeout():
        """Check for session inactivity and logout if exceeded"""
        # Skip for static files and non-authenticated requests
        if request.endpoint and request.endpoint.startswith('static'):
            return None

        if _extract_bearer_token(request):
            return None

        if current_user.is_authenticated:
            last_activity = session.get('last_activity')
            now = datetime.now(timezone.utc).timestamp()

            if last_activity:
                if now - last_activity > SESSION_INACTIVITY_TIMEOUT:
                    # Session expired due to inactivity
                    logout_user()
                    session.clear()
                    if request.is_json or request.path.startswith('/api/'):
                        return jsonify({'error': 'Session expired due to inactivity'}), 401
                    return redirect(url_for('auth_ui.login', next=request.url))

            # Update last activity timestamp
            session['last_activity'] = now

        return None

    @login_manager.user_loader
    def load_user(user_id):
        try:
            user_id, generation = user_id.split(':', 1)
            user = db.session.get(User, int(user_id))
            if user and user.is_active and generation == str(user.session_generation):
                return user
        except (AttributeError, ValueError):
            return None
        return None

    @login_manager.request_loader
    def load_user_from_request(request):
        # Check for API token in Authorization header
        try:
            token = _extract_bearer_token(request)
            if token:
                api_token = _valid_api_token(token)
                if api_token:
                    api_token.update_last_used()
                    return api_token.user
        except Exception as e:
            # Roll back so a failed token lookup/commit doesn't leave the request's
            # session in an aborted state for the rest of the handler.
            try:
                db.session.rollback()
            except Exception:
                pass
            logger.error(f"Error loading user from API token: {e}")

        return None


def auth_required(f):
    """
    Decorator that requires authentication via session or API token.
    This is used for API endpoints that need to support both cookie and token auth.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Allow internal scheduler requests authenticated by shared secret
        if request_uses_internal_auth():
            return f(*args, **kwargs)

        if request.headers.get('Authorization'):
            token = _extract_bearer_token(request)
            if not token:
                return jsonify({'error': 'Authentication required'}), 401
            api_token = _valid_api_token(token)
            if api_token:
                api_token.update_last_used()
                request.current_user = api_token.user
                return f(*args, **kwargs)
            return jsonify({'error': 'Authentication required'}), 401

        # Check session only when no bearer principal is presented.
        if current_user.is_authenticated and current_user.is_active:
            return f(*args, **kwargs)

        # Not authenticated
        return jsonify({'error': 'Authentication required'}), 401

    return decorated_function


def admin_required(f):
    """Decorator that requires admin privileges"""
    @wraps(f)
    @auth_required
    def decorated_function(*args, **kwargs):
        # Scheduler callbacks authenticate with the internal shared secret and
        # have no user principal to evaluate.
        if request_uses_internal_auth():
            return f(*args, **kwargs)
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
    if request.headers.get('Authorization'):
        token = _extract_bearer_token(request)
        if not token:
            return None
        api_token = _valid_api_token(token)
        if api_token:
            return api_token.user
        return None

    if current_user.is_authenticated and current_user.is_active:
        return current_user

    return None

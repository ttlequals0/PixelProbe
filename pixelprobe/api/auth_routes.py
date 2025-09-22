"""
Authentication API routes
Handles login, logout, user management, and API token operations
"""

import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, APIToken
from auth import authenticate_user, check_first_run, create_initial_admin, auth_required, admin_required, get_authenticated_user

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/api/auth/status', methods=['GET'])
def auth_status():
    """Check authentication status and first-run status"""
    is_first_run = check_first_run()
    user = get_authenticated_user(request)

    return jsonify({
        'authenticated': user is not None,
        'first_run': is_first_run,
        'user': user.to_dict() if user else None
    })


@auth_bp.route('/api/auth/setup', methods=['POST'])
def first_run_setup():
    """Initial setup for the admin user on first run"""
    if not check_first_run():
        return jsonify({'error': 'Setup already completed'}), 400

    data = request.get_json()
    password = data.get('password')

    if not password or len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    admin, error = create_initial_admin(password)
    if error:
        return jsonify({'error': error}), 500

    # Log the admin user in automatically
    login_user(admin, remember=True)

    return jsonify({
        'success': True,
        'message': 'Admin user created successfully',
        'user': admin.to_dict()
    })


@auth_bp.route('/api/auth/login', methods=['POST'])
def api_login():
    """API endpoint for user login"""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    remember = data.get('remember', False)

    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400

    # Check if user needs first-time setup
    if username == 'admin':
        admin_user = User.query.filter_by(username='admin').first()
        if admin_user and admin_user.first_setup_required:
            if not password or len(password) < 8:
                return jsonify({
                    'error': 'First-time setup required',
                    'first_setup': True,
                    'message': 'Please set a password (minimum 8 characters)'
                }), 400

            # Set the password for first-time setup
            admin_user.set_password(password)
            admin_user.first_setup_required = False
            admin_user.last_login = datetime.now(timezone.utc)
            db.session.commit()

            # Log them in
            login_user(admin_user, remember=remember)
            return jsonify({
                'success': True,
                'message': 'Password set successfully',
                'user': admin_user.to_dict()
            })

    # Normal authentication
    user = authenticate_user(username, password)
    if not user:
        return jsonify({'error': 'Invalid username or password'}), 401

    login_user(user, remember=remember)

    return jsonify({
        'success': True,
        'user': user.to_dict()
    })


@auth_bp.route('/api/auth/logout', methods=['POST'])
@login_required
def api_logout():
    """API endpoint for user logout"""
    logout_user()
    return jsonify({'success': True, 'message': 'Logged out successfully'})


@auth_bp.route('/api/users', methods=['GET'])
@admin_required
def list_users():
    """List all users (admin only)"""
    users = User.query.all()
    return jsonify({
        'users': [user.to_dict() for user in users]
    })


@auth_bp.route('/api/users', methods=['POST'])
@admin_required
def create_user():
    """Create a new user (admin only)"""
    data = request.get_json()
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    is_admin = data.get('is_admin', True)  # All users are admin by default

    # Validate input
    if not username or not email or not password:
        return jsonify({'error': 'Username, email, and password required'}), 400

    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    # Check if user already exists
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already exists'}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already exists'}), 400

    # Create new user
    user = User(
        username=username,
        email=email,
        is_admin=is_admin
    )
    user.set_password(password)

    try:
        db.session.add(user)
        db.session.commit()
        return jsonify({
            'success': True,
            'user': user.to_dict()
        }), 201
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to create user: {e}")
        return jsonify({'error': 'Failed to create user'}), 500


@auth_bp.route('/api/users/<int:user_id>', methods=['DELETE'])
@admin_required
def delete_user(user_id):
    """Delete a user (admin only)"""
    # Prevent deleting yourself
    user = get_authenticated_user(request)
    if user.id == user_id:
        return jsonify({'error': 'Cannot delete your own account'}), 400

    # Prevent deleting the last admin
    if User.query.filter_by(is_admin=True).count() == 1:
        user_to_delete = User.query.get(user_id)
        if user_to_delete and user_to_delete.is_admin:
            return jsonify({'error': 'Cannot delete the last admin user'}), 400

    user_to_delete = User.query.get(user_id)
    if not user_to_delete:
        return jsonify({'error': 'User not found'}), 404

    try:
        db.session.delete(user_to_delete)
        db.session.commit()
        return jsonify({'success': True, 'message': 'User deleted successfully'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to delete user: {e}")
        return jsonify({'error': 'Failed to delete user'}), 500


@auth_bp.route('/api/users/<int:user_id>/password', methods=['PUT'])
@auth_required
def change_password(user_id):
    """Change user password (users can change their own, admins can change any)"""
    user = get_authenticated_user(request)

    # Users can only change their own password unless they're admin
    if user.id != user_id and not user.is_admin:
        return jsonify({'error': 'Permission denied'}), 403

    data = request.get_json()
    new_password = data.get('new_password')
    current_password = data.get('current_password')

    if not new_password or len(new_password) < 8:
        return jsonify({'error': 'New password must be at least 8 characters'}), 400

    target_user = User.query.get(user_id)
    if not target_user:
        return jsonify({'error': 'User not found'}), 404

    # If changing own password, verify current password
    if user.id == user_id:
        if not current_password or not user.check_password(current_password):
            return jsonify({'error': 'Current password is incorrect'}), 401

    # Set new password
    target_user.set_password(new_password)

    try:
        db.session.commit()
        return jsonify({'success': True, 'message': 'Password updated successfully'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to update password: {e}")
        return jsonify({'error': 'Failed to update password'}), 500


@auth_bp.route('/api/tokens', methods=['GET'])
@auth_required
def list_tokens():
    """List API tokens for the current user"""
    user = get_authenticated_user(request)
    tokens = APIToken.query.filter_by(user_id=user.id).all()

    return jsonify({
        'tokens': [token.to_dict() for token in tokens]
    })


@auth_bp.route('/api/tokens', methods=['POST'])
@auth_required
def create_token():
    """Create a new API token for the current user"""
    user = get_authenticated_user(request)
    data = request.get_json()
    description = data.get('description', '')
    expires_in_days = data.get('expires_in_days')

    token = APIToken(
        user_id=user.id,
        description=description
    )

    if expires_in_days:
        token.expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

    try:
        db.session.add(token)
        db.session.commit()
        return jsonify({
            'success': True,
            'token': token.token,  # Return full token only on creation
            'token_info': token.to_dict()
        }), 201
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to create token: {e}")
        return jsonify({'error': 'Failed to create token'}), 500


@auth_bp.route('/api/tokens/<int:token_id>', methods=['DELETE'])
@auth_required
def delete_token(token_id):
    """Delete an API token"""
    user = get_authenticated_user(request)
    token = APIToken.query.filter_by(id=token_id, user_id=user.id).first()

    if not token:
        return jsonify({'error': 'Token not found'}), 404

    try:
        db.session.delete(token)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Token deleted successfully'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to delete token: {e}")
        return jsonify({'error': 'Failed to delete token'}), 500


# Web routes for login page
@auth_bp.route('/login')
def login():
    """Render login page"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template('login.html', first_run=check_first_run())


@auth_bp.route('/logout')
@login_required
def logout():
    """Web logout route"""
    logout_user()
    return redirect(url_for('auth.login'))
"""
Test suite for authentication functionality
"""

import pytest
import json
from datetime import datetime, timedelta, timezone
from models import db, User, APIToken
from auth import authenticate_user, check_first_run, create_initial_admin


@pytest.fixture
def auth_client(client, app):
    """Client with authentication helpers"""
    with app.app_context():
        # Create tables if they don't exist
        db.create_all()

        # Clear any existing users
        try:
            APIToken.query.delete()
            User.query.delete()
            db.session.commit()
        except Exception as e:
            # Tables might not exist yet
            db.session.rollback()
            db.create_all()

    return client


def test_first_run_detection(auth_client, app):
    """Test that first run is properly detected"""
    with app.app_context():
        # No users should exist
        assert check_first_run() == True

        # Create a user
        user = User(username='testuser', email='test@test.com', is_admin=True)
        user.set_password('testpass123')
        db.session.add(user)
        db.session.commit()

        # Should no longer be first run
        assert check_first_run() == False


def test_password_hashing(app):
    """Test password hashing and verification"""
    with app.app_context():
        user = User(username='testuser', email='test@test.com')
        password = 'MySecurePassword123!'

        # Set password
        user.set_password(password)

        # Password should be hashed, not plain text
        assert user.password_hash != password
        assert len(user.password_hash) > 20

        # Check correct password
        assert user.check_password(password) == True

        # Check incorrect password
        assert user.check_password('WrongPassword') == False
        assert user.check_password('') == False


def test_initial_admin_creation(auth_client, app):
    """Test creation of initial admin user"""
    with app.app_context():
        # Create initial admin
        admin, error = create_initial_admin('AdminPass123!')

        assert error is None
        assert admin is not None
        assert admin.username == 'admin'
        assert admin.is_admin == True
        assert admin.first_setup_required == False

        # Verify password works
        assert admin.check_password('AdminPass123!') == True

        # Should not be able to create another initial admin
        admin2, error2 = create_initial_admin('AnotherPass')
        assert admin2 is None
        assert error2 == "Users already exist"


def test_login_api(auth_client, app):
    """Test login via API"""
    with app.app_context():
        # Create a user
        user = User(username='testuser', email='test@test.com', is_admin=True)
        user.set_password('testpass123')
        db.session.add(user)
        db.session.commit()

    # Test successful login
    response = auth_client.post('/api/auth/login',
        json={'username': 'testuser', 'password': 'testpass123'})

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success'] == True
    assert data['user']['username'] == 'testuser'

    # Test failed login - wrong password
    response = auth_client.post('/api/auth/login',
        json={'username': 'testuser', 'password': 'wrongpass'})

    assert response.status_code == 401
    data = json.loads(response.data)
    assert 'error' in data


def test_first_run_setup_api(auth_client):
    """Test first-run setup via API"""
    # Check first run status
    response = auth_client.get('/api/auth/status')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['first_run'] == True
    assert data['authenticated'] == False

    # Setup admin password
    response = auth_client.post('/api/auth/setup',
        json={'password': 'AdminPass123!'})

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success'] == True
    assert data['user']['username'] == 'admin'

    # Should not be first run anymore
    response = auth_client.get('/api/auth/status')
    data = json.loads(response.data)
    assert data['first_run'] == False


def test_user_management_api(auth_client, app):
    """Test user creation and deletion via API"""
    with app.app_context():
        # Create admin user for authentication
        admin = User(username='admin', email='admin@test.com', is_admin=True)
        admin.set_password('adminpass')
        db.session.add(admin)
        db.session.commit()

    # Login as admin
    auth_client.post('/api/auth/login',
        json={'username': 'admin', 'password': 'adminpass'})

    # Create a new user
    response = auth_client.post('/api/users',
        json={
            'username': 'newuser',
            'email': 'new@test.com',
            'password': 'newpass123',
            'is_admin': False
        })

    assert response.status_code == 201
    data = json.loads(response.data)
    assert data['user']['username'] == 'newuser'
    new_user_id = data['user']['id']

    # List users
    response = auth_client.get('/api/users')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert len(data['users']) == 2

    # Delete the new user
    response = auth_client.delete(f'/api/users/{new_user_id}')
    assert response.status_code == 200

    # Verify user is deleted
    response = auth_client.get('/api/users')
    data = json.loads(response.data)
    assert len(data['users']) == 1


def test_api_token_management(auth_client, app):
    """Test API token creation and deletion"""
    with app.app_context():
        # Create user
        user = User(username='testuser', email='test@test.com', is_admin=True)
        user.set_password('testpass')
        db.session.add(user)
        db.session.commit()

    # Login
    auth_client.post('/api/auth/login',
        json={'username': 'testuser', 'password': 'testpass'})

    # Create API token
    response = auth_client.post('/api/tokens',
        json={'description': 'Test Token', 'expires_in_days': 30})

    assert response.status_code == 201
    data = json.loads(response.data)
    assert 'token' in data
    assert data['token_info']['description'] == 'Test Token'
    token_id = data['token_info']['id']
    api_token = data['token']

    # List tokens
    response = auth_client.get('/api/tokens')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert len(data['tokens']) == 1

    # Use token for authentication
    response = auth_client.get('/api/auth/status',
        headers={'Authorization': f'Bearer {api_token}'})
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['authenticated'] == True

    # Delete token
    response = auth_client.delete(f'/api/tokens/{token_id}')
    assert response.status_code == 200

    # Logout from session to properly test token auth
    auth_client.post('/api/auth/logout')

    # Token should no longer work
    response = auth_client.get('/api/auth/status',
        headers={'Authorization': f'Bearer {api_token}'})
    data = json.loads(response.data)
    assert data['authenticated'] == False


def test_password_change(auth_client, app):
    """Test password change functionality"""
    with app.app_context():
        # Create user
        user = User(username='testuser', email='test@test.com', is_admin=True)
        user.set_password('oldpass123')
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    # Login
    auth_client.post('/api/auth/login',
        json={'username': 'testuser', 'password': 'oldpass123'})

    # Change password
    response = auth_client.put(f'/api/users/{user_id}/password',
        json={
            'current_password': 'oldpass123',
            'new_password': 'newpass456'
        })

    assert response.status_code == 200

    # Logout
    auth_client.post('/api/auth/logout')

    # Old password should not work
    response = auth_client.post('/api/auth/login',
        json={'username': 'testuser', 'password': 'oldpass123'})
    assert response.status_code == 401

    # New password should work
    response = auth_client.post('/api/auth/login',
        json={'username': 'testuser', 'password': 'newpass456'})
    assert response.status_code == 200


def test_authentication_required_for_api(auth_client, app):
    """Test that API endpoints require authentication"""
    # Without authentication, API calls should fail
    response = auth_client.get('/api/stats')
    assert response.status_code == 401

    response = auth_client.get('/api/scan-results')
    assert response.status_code == 401

    with app.app_context():
        # Create and login user
        user = User(username='testuser', email='test@test.com', is_admin=True)
        user.set_password('testpass')
        db.session.add(user)
        db.session.commit()

    auth_client.post('/api/auth/login',
        json={'username': 'testuser', 'password': 'testpass'})

    # With authentication, API calls should work
    response = auth_client.get('/api/stats')
    assert response.status_code == 200


def test_token_expiration(app):
    """Test API token expiration"""
    with app.app_context():
        # Use unique username to avoid conflicts
        user = User(username='tokenexpireuser', email='tokenexpire@test.com')
        user.set_password('testpass')
        db.session.add(user)
        db.session.commit()

        # Create token that expires in the past
        token = APIToken(
            user_id=user.id,
            description='Expired Token',
            expires_at=datetime.now(timezone.utc) - timedelta(days=1)
        )
        db.session.add(token)
        db.session.commit()

        # Token should be invalid
        assert token.is_valid() == False

        # Create token that expires in the future
        token2 = APIToken(
            user_id=user.id,
            description='Valid Token',
            expires_at=datetime.now(timezone.utc) + timedelta(days=1)
        )
        db.session.add(token2)
        db.session.commit()

        # Token should be valid
        assert token2.is_valid() == True
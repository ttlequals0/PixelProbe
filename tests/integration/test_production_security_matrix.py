"""Production app security regressions, isolated in a subprocess."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url


POSTGRES_URI = os.environ.get('PIXELPROBE_TEST_POSTGRES_URI')
REDIS_URI = os.environ.get('PIXELPROBE_TEST_REDIS_URI')
POSTGRES_CONFIG = make_url(POSTGRES_URI) if POSTGRES_URI else None

pytestmark = pytest.mark.skipif(
    not (POSTGRES_URI and REDIS_URI),
    reason='requires PIXELPROBE_TEST_POSTGRES_URI and PIXELPROBE_TEST_REDIS_URI',
)


def test_production_security_matrix():
    root = Path(__file__).parents[2]
    script = r'''
import os
import re
import subprocess
import sys
import app as app_module
from pixelprobe.models import APIToken, SecurityAuditEvent, User, db

app = app_module.app
suffix = os.environ['SECURITY_MATRIX_ID']
admin_username = f'matrix-admin-{suffix}'
member_username = f'matrix-member-{suffix}'
child_rate_test = r"""
import os
import re
import sys
import app as app_module
app = app_module.app
print('READY', flush=True)
assert sys.stdin.readline().strip() == 'run'
client = app.test_client()
remote = '198.51.100.42'
page = client.get('/login', base_url='https://localhost', environ_overrides={'REMOTE_ADDR': remote})
token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.get_data(as_text=True)).group(1)
for _ in range(2):
    response = client.post('/api/auth/login', base_url='https://localhost',
        json={'username': os.environ['MATRIX_ADMIN_USERNAME'], 'password': 'wrong'},
        headers={'X-CSRFToken': token, 'Referer': 'https://localhost/login'},
        environ_overrides={'REMOTE_ADDR': remote})
    assert response.status_code == 401
"""
child_env = os.environ | {'MATRIX_ADMIN_USERNAME': admin_username}
child = subprocess.Popen([sys.executable, '-c', child_rate_test], env=child_env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
assert child.stdout.readline().strip() == 'READY'

with app.app_context():
    admin = User(username=admin_username, email=f'{admin_username}@example.test', is_admin=True)
    admin.set_password('matrix-password')
    member = User(username=member_username, email=f'{member_username}@example.test', is_admin=False)
    member.set_password('matrix-password')
    db.session.add_all([admin, member])
    db.session.commit()
    admin_id = admin.id
    member_id = member.id
    member_token = APIToken(user_id=member.id)
    db.session.add(member_token)
    admin_token = APIToken(user_id=admin.id)
    db.session.add(admin_token)
    db.session.commit()
    bearer = member_token.plaintext_token
    admin_bearer = admin_token.plaintext_token

def csrf(client, remote=None):
    page = client.get('/login', base_url='https://localhost',
                      environ_overrides={'REMOTE_ADDR': remote} if remote else None)
    return re.search(r'<meta name="csrf-token" content="([^"]+)"', page.get_data(as_text=True)).group(1)

cookie_client = app.test_client()
token = csrf(cookie_client)
login = cookie_client.post('/api/auth/login', base_url='https://localhost',
    json={'username': admin_username, 'password': 'matrix-password', 'remember': True},
    headers={'X-CSRFToken': token, 'Referer': 'https://localhost/login'})
assert login.status_code == 200
cookies = '\n'.join(login.headers.getlist('Set-Cookie'))
auth_cookies = [cookie for cookie in login.headers.getlist('Set-Cookie')
                if cookie.startswith(('session=', 'remember_token='))]
assert len(auth_cookies) == 2
assert all('Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=Lax' in cookie
           for cookie in auth_cookies)
assert any(cookie.startswith('remember_token=') and 'Expires=' in cookie
           for cookie in auth_cookies)
assert app.config['REMEMBER_COOKIE_DURATION'].days == 30
member_cookie_client = app.test_client()
member_csrf_token = csrf(member_cookie_client, '127.0.0.9')
member_login = member_cookie_client.post('/api/auth/login', base_url='https://localhost',
    json={'username': member_username, 'password': 'matrix-password'},
    headers={'X-CSRFToken': member_csrf_token,
             'Referer': 'https://localhost/login'},
    environ_overrides={'REMOTE_ADDR': '127.0.0.9'})
assert member_login.status_code == 200
for endpoint in ('/api/users', '/api/settings', '/api/configurations', '/api/schedules'):
    assert member_cookie_client.get(endpoint, base_url='https://localhost').status_code == 403
for endpoint in ('/api/notifications/providers', '/api/notifications/rules',
                 '/api/healthcheck', '/api/exclusions'):
    assert member_cookie_client.get(endpoint, base_url='https://localhost').status_code == 403
member_write_headers = {
    'X-CSRFToken': member_csrf_token,
    'Referer': 'https://localhost/login',
}
assert member_cookie_client.post('/api/configurations', base_url='https://localhost',
    json={'path': '/tmp'}, headers=member_write_headers,
    environ_overrides={'REMOTE_ADDR': '127.0.0.9'}).status_code == 403
for endpoint, payload in (
    ('/api/notifications/providers', {}),
    ('/api/notifications/rules', {}),
    ('/api/healthcheck', {}),
    ('/api/logs/purge', {}),
    ('/api/cleanup-orphaned', {}),
    ('/api/notifications/providers/999/test', {}),
    ('/api/healthcheck/999/test', {}),
    ('/api/exclusions/paths', {}),
    ('/api/cancel-cleanup', {}),
    ('/api/reset-cleanup-state', {}),
    ('/api/cancel-file-changes', {}),
    ('/api/reset-file-changes-state', {}),
    ('/api/file-changes', {}),
    ('/api/vacuum', {}),
):
    assert member_cookie_client.post(endpoint, base_url='https://localhost',
                                     json=payload, headers=member_write_headers).status_code == 403
for endpoint, payload in (
    ('/api/logs/retention', {'retention_days': 30}),
    ('/api/exclusions', {}),
    ('/api/notifications/providers/999', {}),
    ('/api/notifications/rules/999', {}),
    ('/api/healthcheck/999', {}),
):
    assert member_cookie_client.put(endpoint, base_url='https://localhost',
                                    json=payload, headers=member_write_headers).status_code == 403
assert cookie_client.get('/api/configurations', base_url='https://localhost').status_code == 200
assert cookie_client.post('/api/configurations', base_url='https://localhost',
    json={'path': ''}, headers={'X-CSRFToken': token, 'Referer': 'https://localhost/login'}).status_code == 400
csrf_required = app.test_client().post('/api/auth/login', base_url='https://localhost',
    json={'username': admin_username, 'password': 'matrix-password'},
    headers={'Authorization': 'Bearer ' + bearer})
assert csrf_required.status_code == 400
assert cookie_client.post('/api/auth/logout', base_url='https://localhost',
    headers={'Authorization': 'Bearer ' + bearer}).status_code == 400
assert cookie_client.post('/api/tokens', json={}, base_url='https://localhost').status_code == 400
assert cookie_client.get('/api/tokens', headers={'Authorization': 'Bearer invalid'},
    base_url='https://localhost').status_code == 401
assert cookie_client.get('/api/users', headers={'Authorization': 'Bearer ' + bearer},
    base_url='https://localhost').status_code == 403
assert cookie_client.get('/api/settings', headers={'Authorization': 'Bearer ' + bearer},
    base_url='https://localhost').status_code == 403
assert cookie_client.get('/api/configurations', headers={'Authorization': 'Bearer ' + bearer},
    base_url='https://localhost').status_code == 403
assert cookie_client.get('/api/schedules', headers={'Authorization': 'Bearer ' + bearer},
    base_url='https://localhost').status_code == 403
for endpoint in ('/api/notifications/providers', '/api/notifications/rules',
                 '/api/healthcheck', '/api/exclusions'):
    assert cookie_client.get(endpoint, headers={'Authorization': 'Bearer ' + bearer},
                             base_url='https://localhost').status_code == 403
assert cookie_client.post('/api/configurations', headers={'Authorization': 'Bearer ' + bearer},
    base_url='https://localhost', json={'path': '/tmp'}).status_code == 403
for endpoint, payload in (
    ('/api/notifications/providers', {}),
    ('/api/notifications/rules', {}),
    ('/api/healthcheck', {}),
    ('/api/logs/purge', {}),
    ('/api/cleanup-orphaned', {}),
    ('/api/notifications/providers/999/test', {}),
    ('/api/healthcheck/999/test', {}),
    ('/api/exclusions/paths', {}),
    ('/api/cancel-cleanup', {}),
    ('/api/reset-cleanup-state', {}),
    ('/api/cancel-file-changes', {}),
    ('/api/reset-file-changes-state', {}),
    ('/api/file-changes', {}),
    ('/api/vacuum', {}),
):
    assert cookie_client.post(endpoint, headers={'Authorization': 'Bearer ' + bearer},
                               base_url='https://localhost', json=payload).status_code == 403
for endpoint, payload in (
    ('/api/logs/retention', {'retention_days': 30}),
    ('/api/exclusions', {}),
    ('/api/notifications/providers/999', {}),
    ('/api/notifications/rules/999', {}),
    ('/api/healthcheck/999', {}),
):
    assert cookie_client.put(endpoint, headers={'Authorization': 'Bearer ' + bearer},
                              base_url='https://localhost', json=payload).status_code == 403
assert cookie_client.get('/api/settings', headers={'Authorization': 'Bearer ' + admin_bearer},
    base_url='https://localhost').status_code == 200
assert cookie_client.get('/api/configurations', headers={'Authorization': 'Bearer ' + admin_bearer},
    base_url='https://localhost').status_code == 200
assert cookie_client.get('/logout', base_url='https://localhost').status_code == 405
assert cookie_client.get('/api/tokens', base_url='https://localhost').status_code == 200

bearer_client = app.test_client()
created = bearer_client.post('/api/tokens', base_url='https://localhost',
    json={'description': 'matrix'}, headers={'Authorization': 'Bearer ' + bearer})
assert created.status_code == 201
with app.app_context():
    event = SecurityAuditEvent.query.filter_by(action='api_token_created').order_by(
        SecurityAuditEvent.id.desc()).first()
    assert event.actor_id == member_id
assert bearer_client.post('/api/file-changes', base_url='https://localhost',
    headers={'Authorization': 'Bearer ' + bearer}).status_code == 403
assert bearer_client.get('/api/file-changes', base_url='https://localhost',
    headers={'Authorization': 'Bearer ' + bearer}).status_code == 405

with app.app_context():
    db.session.get(User, member_id).is_active = False
    db.session.commit()
inactive_response = bearer_client.get('/api/tokens', base_url='https://localhost',
    headers={'Authorization': 'Bearer ' + bearer})
assert inactive_response.status_code == 401, inactive_response.status_code

assert app_module.inject_assets()['asset_url']('app.js').startswith('/static/dist/')
old_manifest = app_module._webpack_manifest
app_module._webpack_manifest = {}
assets = app_module.inject_assets()['asset_url']
assert assets('app.js').startswith('/static/js/app.js?')
assert assets('styles.css').startswith('/static/css/styles.css?')
app_module._webpack_manifest = old_manifest

rate_remote = '198.51.100.42'
for _ in range(2):
    client = app.test_client()
    response = client.post('/api/auth/login', base_url='https://localhost',
        json={'username': admin_username, 'password': 'wrong'},
        headers={'X-CSRFToken': csrf(client, rate_remote), 'Referer': 'https://localhost/login'},
        environ_overrides={'REMOTE_ADDR': rate_remote})
    assert response.status_code == 401
child.stdin.write('run\n')
child.stdin.flush()
child_stdout, child_stderr = child.communicate(timeout=30)
assert child.returncode == 0, child_stdout + child_stderr
client = app.test_client()
response = client.post('/api/auth/login', base_url='https://localhost',
    json={'username': admin_username, 'password': 'wrong'},
    headers={'X-CSRFToken': csrf(client, rate_remote), 'Referer': 'https://localhost/login'},
    environ_overrides={'REMOTE_ADDR': rate_remote})
assert response.status_code == 401, response.status_code
response = client.post('/api/auth/login', base_url='https://localhost',
    json={'username': admin_username, 'password': 'wrong'},
    headers={'X-CSRFToken': csrf(client, rate_remote), 'Referer': 'https://localhost/login'},
    environ_overrides={'REMOTE_ADDR': rate_remote})
assert response.status_code == 429, response.status_code

remember_client = app.test_client()
remember_login = remember_client.post('/api/auth/login', base_url='https://localhost',
    json={'username': admin_username, 'password': 'matrix-password', 'remember': True},
    headers={'X-CSRFToken': csrf(remember_client, '127.0.0.2'), 'Referer': 'https://localhost/login'},
    environ_overrides={'REMOTE_ADDR': '127.0.0.2'})
assert remember_login.status_code == 200
remember_client.delete_cookie('session', domain='localhost')
assert remember_client.get('/api/tokens', base_url='https://localhost').status_code == 200

old_session_client = app.test_client()
old_login = old_session_client.post('/api/auth/login', base_url='https://localhost',
    json={'username': admin_username, 'password': 'matrix-password'},
    headers={'X-CSRFToken': csrf(old_session_client, '127.0.0.2'), 'Referer': 'https://localhost/login'},
    environ_overrides={'REMOTE_ADDR': '127.0.0.2'})
assert old_login.status_code == 200
rotation = cookie_client.put(f'/api/users/{admin_id}/password', base_url='https://localhost',
    json={'current_password': 'matrix-password', 'new_password': 'matrix-password-rotated'},
    headers={'X-CSRFToken': token, 'Referer': 'https://localhost/login'})
assert rotation.status_code == 200
assert old_session_client.get('/api/tokens', base_url='https://localhost').status_code == 401
assert cookie_client.get('/api/tokens', base_url='https://localhost').status_code == 200

from flask import Flask
from pixelprobe.auth import init_auth
os.environ['SESSION_COOKIE_SECURE'] = 'false'
http_app = Flask('http-cookie-override')
http_app.config['SECRET_KEY'] = 'http-cookie-secret'
init_auth(http_app)
assert http_app.config['SESSION_COOKIE_SECURE'] is False
assert http_app.config['REMEMBER_COOKIE_SECURE'] is False
os.environ['SESSION_COOKIE_SECURE'] = 'true'
'''
    env = os.environ | {
        'SECRET_KEY': 'production-security-matrix-secret',
        'FLASK_ENV': 'production',
        'DATABASE_URL': POSTGRES_URI,
        'POSTGRES_HOST': POSTGRES_CONFIG.host,
        'POSTGRES_PORT': str(POSTGRES_CONFIG.port or 5432),
        'POSTGRES_DB': POSTGRES_CONFIG.database,
        'POSTGRES_USER': POSTGRES_CONFIG.username,
        'POSTGRES_PASSWORD': POSTGRES_CONFIG.password or '',
        'CELERY_BROKER_URL': REDIS_URI,
        'RATELIMIT_STORAGE_URI': REDIS_URI,
        'RATELIMIT_KEY_PREFIX': f'production-matrix-{uuid.uuid4().hex}',
        'SECURITY_MATRIX_ID': uuid.uuid4().hex,
        'SCHEDULER_ENABLED': 'false',
    }
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=root, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

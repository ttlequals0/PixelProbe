"""Ensure OpenAPI documents every production API operation."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


HTTP_OPERATIONS = {'get', 'post', 'put', 'patch', 'delete'}

# Flask creates these rules rather than application APIs. UI pages are outside
# the /api contract. Each exception is named so a new route cannot disappear
# from the parity check without a deliberate review.
ROUTE_EXCEPTIONS = {
    ('/', 'get'): 'authenticated HTML application page',
    ('/api-docs', 'get'): 'authenticated HTML OpenAPI viewer',
    ('/favicon.ico', 'get'): 'static browser asset',
    ('/static/{filename}', 'get'): 'Flask static asset rule',
    ('/static/images/pixelprobe-logo.svg', 'get'): 'static browser asset',
    ('/login', 'get'): 'authenticated UI login page',
    ('/logout', 'post'): 'authenticated UI logout page',
}


def _normalized_path(flask_path):
    path = re.sub(r'<(?:[^:>]+:)?([^>]+)>', r'{\1}', flask_path)
    if path == '/api':
        return '/'
    if path.startswith('/api/'):
        return path[4:]
    return path


def _registered_operations(app):
    return {
        (_normalized_path(rule.rule), method.lower())
        for rule in app.url_map.iter_rules()
        for method in rule.methods - {'HEAD', 'OPTIONS'}
    }


def _documented_operations(spec):
    return {
        (path, method.lower())
        for path, item in spec['paths'].items()
        for method in item
        if method.lower() in HTTP_OPERATIONS
    }


def _assert_complete_parity(registered, documented):
    undocumented = registered - documented - set(ROUTE_EXCEPTIONS)
    stale = documented - registered
    assert not undocumented, f'Undocumented application operations: {sorted(undocumented)}'
    assert not stale, f'OpenAPI operations without a registered route: {sorted(stale)}'


def test_openapi_covers_all_registered_operations(app):
    spec = yaml.safe_load(Path('openapi.yaml').read_text(encoding='utf-8'))
    _assert_complete_parity(_registered_operations(app), _documented_operations(spec))


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get('PIXELPROBE_TEST_POSTGRES_URI'),
    reason='PIXELPROBE_TEST_POSTGRES_URI not set',
)
def test_openapi_covers_production_app_registration():
    """Import the production app with PostgreSQL and compare its actual URL map."""
    script = '''
import json
from app import app
print(json.dumps([
    [rule.rule, sorted(method for method in rule.methods if method not in {'HEAD', 'OPTIONS'})]
    for rule in app.url_map.iter_rules()
]))
'''
    env = os.environ.copy()
    env.update({
        'DATABASE_URL': os.environ['PIXELPROBE_TEST_POSTGRES_URI'],
        'SECRET_KEY': 'openapi-production-route-parity',
        'SCHEDULER_ENABLED': 'false',
        'FLASK_ENV': 'testing',
    })
    redis_uri = os.environ.get('PIXELPROBE_TEST_REDIS_URI')
    if redis_uri:
        env['CELERY_BROKER_URL'] = redis_uri

    completed = subprocess.run(
        [sys.executable, '-c', script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    route_data = json.loads(completed.stdout.strip().splitlines()[-1])
    registered = {
        (_normalized_path(rule), method.lower())
        for rule, methods in route_data
        for method in methods
    }
    spec = yaml.safe_load(Path('openapi.yaml').read_text(encoding='utf-8'))
    _assert_complete_parity(registered, _documented_operations(spec))

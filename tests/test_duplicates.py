import os
from uuid import uuid4
from datetime import datetime, timezone

import pytest
from flask import Flask
from sqlalchemy import create_engine, text

from pixelprobe.models import (db as model_db, DuplicateGroupSummary,
                               DuplicateIndexState, ScanResult)
from pixelprobe.services.duplicate_service import (_duplicate_groups,
                                                   duplicate_index_status,
                                                   duplicate_members,
                                                   duplicate_statistics,
                                                   refresh_duplicate_index)
from pixelprobe.services.stats_service import StatsService


def seed_duplicates(session):
    rows = [
        ScanResult(file_path='/media/a/same.mp4', file_hash='a' * 64, file_size=10),
        ScanResult(file_path='/media/b/same.mp4', file_hash='a' * 64, file_size=10),
        ScanResult(file_path='/other/copy.mp4', file_hash='a' * 64, file_size=10),
        ScanResult(file_path='/media/different-size.mp4', file_hash='a' * 64, file_size=11),
        ScanResult(file_path='/media/missing/same.mp4', file_hash='a' * 64, file_size=10, file_exists=False),
        ScanResult(file_path='/media/suspect.mp4', file_hash='a' * 64, file_size=10, bitrot_suspected=True),
        ScanResult(file_path='/media/error.mp4', file_hash='a' * 64, file_size=10, last_integrity_outcome='error'),
        ScanResult(file_path='/media/unreadable.mp4', file_hash='a' * 64, file_size=10, last_integrity_outcome='unreadable'),
        ScanResult(file_path='/media/failed-scan.mp4', file_hash='a' * 64, file_size=10, scan_status='failed'),
        ScanResult(file_path='/media/legacy-error.mp4', file_hash='a' * 64, file_size=10, scan_status='completed', scan_tool='error'),
        ScanResult(file_path='/media/blank.mp4', file_hash=' ', file_size=10),
        ScanResult(file_path='/media/no-hash/same.mp4', file_size=10),
        ScanResult(file_path='/media/Same.mp4', file_size=10),
    ]
    session.add_all(rows)
    session.commit()
    return rows


def prime_sqlite_cache(session):
    for mode in ('hash', 'name'):
        groups, _ = _duplicate_groups(mode)
        for row in session.query(groups).all():
            session.add(DuplicateGroupSummary(
                mode=mode,
                key_text=row.key_0,
                key_size=row.key_1 if mode == 'hash' else -1,
                group_size=row.group_size,
                group_id=row.group_id,
            ))
    session.add(DuplicateIndexState(id=1, ready=True,
                                    updated_at=datetime.now(timezone.utc)))
    session.commit()


def assert_grouping(session, prime=True):
    seed_duplicates(session)
    if prime:
        prime_sqlite_cache(session)
    stats = duplicate_statistics()
    assert stats == {
        'duplicate_files': 3, 'duplicate_groups': 1, 'duplicate_extra_files': 2,
        'filename_duplicate_files': 3, 'filename_duplicate_groups': 1,
        'filename_duplicate_extra_files': 2,
    }
    for mode in ('hash', 'name'):
        members = duplicate_members(mode)
        assert session.query(members).count() == 3
        assert {row.group_size for row in session.query(members)} == {3}


def test_duplicate_eligibility_and_count_semantics(db):
    assert_grouping(db.session)
    assert StatsService().get_file_statistics()['duplicate_files'] == 3


def test_duplicate_api_waits_for_cache_before_serving_duplicate_results(
        db, authenticated_client):
    status = duplicate_index_status()
    assert status == {'ready': False, 'updated_at': None, 'stale': False}
    response = authenticated_client.get('/api/scan-results?duplicate_mode=hash')
    assert response.status_code == 503
    assert response.headers['Retry-After'] == '30'
    assert response.get_json() == {
        'error': 'Duplicate index is initializing',
        'duplicate_status': status,
    }
    stats = authenticated_client.get('/api/stats').get_json()
    assert stats['duplicate_files'] is None
    assert stats['duplicate_status']['ready'] is False


def test_duplicate_api_global_groups_pagination_and_stats(db, authenticated_client, monkeypatch):
    seed_duplicates(db.session)
    prime_sqlite_cache(db.session)
    monkeypatch.setattr('pixelprobe.api.scan_routes.get_configured_scan_paths', lambda: ['/media'])
    response = authenticated_client.get('/api/scan-results?duplicate_mode=hash&path=/media&per_page=1&sort_field=file_path&sort_order=asc')
    assert response.status_code == 200
    data = response.get_json()
    assert data['total'] == 2
    assert data['pages'] == 2
    assert data['results'][0]['duplicate_group_size'] == 3
    assert data['results'][0]['duplicate_mode'] == 'hash'
    second = authenticated_client.get('/api/scan-results?duplicate_mode=hash&path=/media&per_page=1&page=2&sort_field=file_path&sort_order=asc').get_json()
    assert second['results'][0]['id'] != data['results'][0]['id']
    named = authenticated_client.get('/api/scan-results?duplicate_mode=name&search=no-hash').get_json()
    assert named['total'] == 1
    assert named['results'][0]['duplicate_group_size'] == 3
    filtered = authenticated_client.get('/api/scan-results?duplicate_mode=hash&scan_status=pending&is_corrupted=true')
    assert filtered.status_code == 200
    assert filtered.get_json()['total'] == 0
    stats = authenticated_client.get('/api/stats').get_json()
    assert stats['duplicate_files'] == 3
    assert stats['filename_duplicate_files'] == 3
    assert authenticated_client.get('/api/scan-results?duplicate_mode=visual').status_code == 400


def test_scan_results_json_preserves_script_like_search_and_filename(
        db, authenticated_client):
    html_like = '<img src=x onerror=alert(1)>'
    result = ScanResult(file_path=f'/media/{html_like}.mp4', file_size=10)
    db.session.add(result)
    db.session.commit()

    response = authenticated_client.get(
        '/api/scan-results', query_string={'search': html_like})

    assert response.status_code == 200
    assert response.content_type == 'application/json'
    assert response.get_json()['results'][0]['file_name'] == f'{html_like}.mp4'


def test_scan_results_invalid_path_returns_json_empty_response(
        db, authenticated_client, monkeypatch):
    monkeypatch.setattr(
        'pixelprobe.api.scan_routes.get_configured_scan_paths', lambda: ['/media'])

    response = authenticated_client.get(
        '/api/scan-results', query_string={'path': '/outside/<script>alert(1)</script>'})

    assert response.status_code == 200
    assert response.content_type == 'application/json'
    assert response.get_json() == {
        'results': [], 'total': 0, 'page': 1, 'per_page': 100, 'pages': 0,
    }


@pytest.mark.postgres
@pytest.mark.skipif(not os.environ.get('PIXELPROBE_TEST_POSTGRES_URI'), reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_postgres_duplicate_basename_and_grouping():
    uri = os.environ['PIXELPROBE_TEST_POSTGRES_URI']
    schema = f'duplicates_{uuid4().hex[:12]}'
    engine = create_engine(uri)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI=uri,
                      SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'options': f'-csearch_path={schema}'}})
    model_db.init_app(app)
    try:
        with app.app_context():
            model_db.create_all()
            from pixelprobe.migrations.duplicates import run_duplicate_index_migrations
            run_duplicate_index_migrations(model_db)
            seed_duplicates(model_db.session)
            assert refresh_duplicate_index()
            assert duplicate_index_status()['ready'] is True
            assert refresh_duplicate_index()
            assert duplicate_statistics()['duplicate_files'] == 3
            stats = duplicate_statistics()
            assert stats == {
                'duplicate_files': 3, 'duplicate_groups': 1,
                'duplicate_extra_files': 2,
                'filename_duplicate_files': 3, 'filename_duplicate_groups': 1,
                'filename_duplicate_extra_files': 2,
            }
            for mode in ('hash', 'name'):
                members = duplicate_members(mode)
                assert model_db.session.query(members).count() == 3
                assert {row.group_size for row in model_db.session.query(members)} == {3}
            model_db.session.remove()
            model_db.engine.dispose()
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()

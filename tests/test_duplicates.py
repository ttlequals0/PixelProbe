import os
from uuid import uuid4

import pytest
from flask import Flask
from sqlalchemy import create_engine, text

from pixelprobe.models import db as model_db, ScanResult
from pixelprobe.services.duplicate_service import duplicate_members, duplicate_statistics
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


def assert_grouping(session):
    seed_duplicates(session)
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


def test_duplicate_api_global_groups_pagination_and_stats(db, authenticated_client, monkeypatch):
    seed_duplicates(db.session)
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
            assert_grouping(model_db.session)
            model_db.session.remove()
            model_db.engine.dispose()
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()

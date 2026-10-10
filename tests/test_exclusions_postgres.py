"""PostgreSQL coverage for the one-time exclusion migration and live policy."""

import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import Flask
from sqlalchemy import create_engine, text

from pixelprobe.models import Exclusion, AppConfig, db

POSTGRES_URI = os.environ.get('PIXELPROBE_TEST_POSTGRES_URI')


@pytest.fixture
def exclusion_database(tmp_path, monkeypatch):
    from pixelprobe.migrations import startup

    schema = f"exclusions_{uuid4().hex[:12]}"
    admin_engine = create_engine(POSTGRES_URI)
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))

    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=POSTGRES_URI,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={
            'connect_args': {'options': f'-csearch_path={schema}'}},
    )
    db.init_app(app)

    try:
        with app.app_context():
            db.create_all()
            migration_db = SimpleNamespace(engine=db.engine, metadata=db.metadata)
            json_path = tmp_path / 'legacy-exclusions.json'
            monkeypatch.setattr(startup, 'LEGACY_EXCLUSIONS_FILE', str(json_path))
            monkeypatch.delenv('EXCLUDED_PATHS', raising=False)
            monkeypatch.delenv('EXCLUDED_EXTENSIONS', raising=False)
            try:
                yield app, migration_db, json_path
            finally:
                db.session.remove()
                db.engine.dispose()
    finally:
        with admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin_engine.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_legacy_exclusions_import_once_and_database_tombstones_win(
        exclusion_database, monkeypatch):
    from pixelprobe.migrations.startup import run_v2_10_3_exclusion_migration
    from pixelprobe.media_checker import load_exclusions_with_patterns

    app, migration_db, json_path = exclusion_database
    json_path.write_text(json.dumps({
        'paths': ['/media/from-json', '/media/hidden'],
        'extensions': ['.tmp', '.MP4'],
        'filename_patterns': ['sample-*', 'Sample-*'],
    }), encoding='utf-8')
    monkeypatch.setenv('EXCLUDED_PATHS', '/media/from-env')
    monkeypatch.setenv('EXCLUDED_EXTENSIONS', '.PART')

    with app.app_context():
        db.session.add(Exclusion(
            exclusion_type='path', value='/media/hidden', is_active=False))
        db.session.commit()

        run_v2_10_3_exclusion_migration(migration_db)
        run_v2_10_3_exclusion_migration(migration_db)

        active = {(row.exclusion_type, row.value) for row in Exclusion.query
                  if row.is_active}
        assert active == {
            ('path', '/media/from-json'),
            ('path', '/media/from-env'),
            ('extension', '.tmp'),
            ('extension', '.MP4'),
            ('extension', '.part'),
            ('filename_pattern', 'sample-*'),
            ('filename_pattern', 'Sample-*'),
        }
        assert Exclusion.query.filter_by(
            exclusion_type='path', value='/media/hidden').one().is_active is False
        assert db.session.query(AppConfig).filter_by(
            key='legacy_exclusions_imported_v1').count() == 1

        paths, extensions, patterns = load_exclusions_with_patterns()
        assert set(paths) == {'/media/from-json', '/media/from-env'}
        assert set(extensions) == {'.tmp', '.MP4', '.part'}
        assert 'sample-*' in patterns
        assert 'Sample-*' in patterns
        assert {'.DS_Store', 'Thumbs.db', '._*', '.gitkeep', '.placeholder'} <= set(patterns)

        # A new app context observes the policy through a separate session.
        db.session.remove()
        with app.app_context():
            paths, extensions, patterns = load_exclusions_with_patterns()
            assert '/media/from-env' in paths
            assert '.part' in extensions
            assert 'Sample-*' in patterns

        # A later malformed legacy file is ignored after the marker is stored.
        json_path.write_text('{invalid', encoding='utf-8')
        run_v2_10_3_exclusion_migration(migration_db)

        # Even a physical row deletion cannot cause the legacy source to reseed it.
        Exclusion.query.filter_by(value='/media/from-json').delete()
        db.session.commit()
        run_v2_10_3_exclusion_migration(migration_db)
        assert Exclusion.query.filter_by(value='/media/from-json').count() == 0


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_invalid_legacy_exclusions_fail_without_rows_or_marker(
        exclusion_database):
    from pixelprobe.migrations.startup import run_v2_10_3_exclusion_migration

    app, migration_db, json_path = exclusion_database
    json_path.write_text(json.dumps({
        'paths': ['/media/should-not-import'],
        'extensions': 'not-a-list',
    }), encoding='utf-8')

    with app.app_context():
        with pytest.raises(ValueError, match='extensions'):
            run_v2_10_3_exclusion_migration(migration_db)
        assert Exclusion.query.count() == 0
        assert AppConfig.query.filter_by(
            key='legacy_exclusions_imported_v1').count() == 0


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_unreadable_legacy_exclusions_fail_without_rows_or_marker(
        exclusion_database):
    from pixelprobe.migrations.startup import run_v2_10_3_exclusion_migration

    app, migration_db, json_path = exclusion_database
    json_path.mkdir()

    with app.app_context():
        with pytest.raises(OSError):
            run_v2_10_3_exclusion_migration(migration_db)
        assert Exclusion.query.count() == 0
        assert AppConfig.query.filter_by(
            key='legacy_exclusions_imported_v1').count() == 0

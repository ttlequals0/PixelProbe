"""
Concurrency regression: parallel scan workers sharing one PixelProbe instance
must persist results safely against a real PostgreSQL backend. StaticPool
previously shared a single raw psycopg2 connection across worker threads.

Requires PIXELPROBE_TEST_POSTGRES_URI (provided by CI's postgres service);
skipped otherwise.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4
from urllib.parse import quote

import pytest
from PIL import Image
from sqlalchemy import create_engine, text
from flask import Flask

from pixelprobe.media_checker import PixelProbe
from pixelprobe.models import db, ScanState
from pixelprobe.services.scan_engine import claim_scan_slot
from pixelprobe.services.scan_service import ScanService

POSTGRES_URI = os.environ.get('PIXELPROBE_TEST_POSTGRES_URI')


def _migration_db(schema):
    engine = create_engine(
        POSTGRES_URI,
        connect_args={'options': f'-csearch_path={schema}'},
    )
    return SimpleNamespace(engine=engine, metadata=db.metadata)


@pytest.mark.postgres
@pytest.mark.timeout(300)
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_concurrent_postgres_startup_waits_for_full_schema_readiness():
    """Only one startup owns DDL; its waiter sees every modeled column."""
    from pixelprobe.migrations import startup

    schema = f"migration_{uuid4().hex[:12]}"
    admin_engine = create_engine(POSTGRES_URI)
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    migration_db = _migration_db(schema)
    gate = Barrier(2)

    def start():
        gate.wait()
        startup.migrate_database(migration_db)
        with migration_db.engine.connect() as conn:
            startup.verify_schema_ready(migration_db, conn)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: start(), range(2)))
    finally:
        migration_db.engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin_engine.dispose()


@pytest.mark.postgres
@pytest.mark.timeout(180)
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_fresh_postgres_schema_completes_full_startup_migrations():
    from pixelprobe.migrations import startup

    schema = f"fresh_migration_{uuid4().hex[:12]}"
    admin_engine = create_engine(POSTGRES_URI)
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    migration_db = _migration_db(schema)
    try:
        startup.migrate_database(migration_db)
        with migration_db.engine.connect() as conn:
            startup.verify_schema_ready(migration_db, conn)
    finally:
        migration_db.engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin_engine.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_lifecycle_migration_repairs_known_legacy_tool_statuses():
    from pixelprobe.migrations import startup

    schema = f"legacy_status_{uuid4().hex[:12]}"
    admin_engine = create_engine(POSTGRES_URI)
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    migration_db = _migration_db(schema)
    try:
        startup.migrate_database(migration_db)
        with migration_db.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO scan_results "
                "(file_path, scan_status, scan_tool, file_hash, marked_as_good, "
                "has_warnings, file_exists) "
                "VALUES ('/legacy/error.mkv', 'completed', 'error', NULL, false, false, true), "
                "('/legacy/unsupported.bin', 'completed', 'unsupported', NULL, false, false, true), "
                "('/legacy/verified.mkv', 'completed', 'ffmpeg', 'known-hash', false, false, true)"))

        startup.run_v2_8_12_lifecycle_migrations(migration_db)

        with migration_db.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT file_path, scan_status, file_hash, last_integrity_success_at "
                "FROM scan_results ORDER BY file_path")).all()
        assert [(row.file_path, row.scan_status, row.file_hash,
                 row.last_integrity_success_at) for row in rows] == [
            ('/legacy/error.mkv', 'error', None, None),
            ('/legacy/unsupported.bin', 'unsupported', None, None),
            ('/legacy/verified.mkv', 'completed', 'known-hash', None),
        ]
    finally:
        migration_db.engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin_engine.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_migration_failure_releases_actual_postgres_advisory_lock(monkeypatch):
    from pixelprobe.migrations import startup

    engine = create_engine(POSTGRES_URI)
    migration_db = SimpleNamespace(engine=engine, metadata=db.metadata)
    monkeypatch.setattr(startup, '_run_all_migrations',
                        lambda *_args: (_ for _ in ()).throw(RuntimeError('injected failure')))

    with pytest.raises(RuntimeError, match='injected failure'):
        startup.migrate_database(migration_db)

    with engine.connect() as conn:
        acquired = conn.execute(
            text('SELECT pg_try_advisory_lock(:lock_id)'),
            {'lock_id': startup.MIGRATION_ADVISORY_LOCK_ID},
        ).scalar()
        assert acquired is True
        conn.execute(text('SELECT pg_advisory_unlock(:lock_id)'),
                     {'lock_id': startup.MIGRATION_ADVISORY_LOCK_ID})
    engine.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_audit_migration_keeps_evidence_when_an_actor_is_deleted():
    from pixelprobe.migrations.security_audit import migrate_security_audit_schema

    schema = f"audit_{uuid4().hex[:12]}"
    admin_engine = create_engine(POSTGRES_URI)
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_engine(
        POSTGRES_URI, connect_args={'options': f'-csearch_path={schema}'})
    migration_db = SimpleNamespace(engine=engine, metadata=db.metadata)
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE users (id SERIAL PRIMARY KEY)'))
            conn.execute(text(
                'CREATE TABLE security_audit_events ('
                'id SERIAL PRIMARY KEY, actor_id INTEGER REFERENCES users(id), '
                "action VARCHAR(100) NOT NULL DEFAULT 'test', "
                "outcome VARCHAR(30) NOT NULL DEFAULT 'ok', "
                "details JSON NOT NULL DEFAULT '{}'::json, "
                'created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP)'))
            actor_id = conn.execute(text('INSERT INTO users DEFAULT VALUES RETURNING id')).scalar_one()
            conn.execute(text('INSERT INTO security_audit_events (actor_id) VALUES (:id)'),
                         {'id': actor_id})

        migrate_security_audit_schema(migration_db)

        with engine.begin() as conn:
            conn.execute(text('DELETE FROM users WHERE id = :id'), {'id': actor_id})
            assert conn.execute(text(
                'SELECT count(*) FROM security_audit_events WHERE actor_id = :id'),
                {'id': actor_id}).scalar_one() == 1
    finally:
        engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin_engine.dispose()


@pytest.mark.postgres
@pytest.mark.timeout(300)
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_parallel_scan_file_saves_are_connection_safe(tmp_path):
    engine = create_engine(POSTGRES_URI)
    db.metadata.create_all(engine)

    files = []
    for i in range(50):
        p = tmp_path / f'img_{i}.png'
        Image.new('RGB', (32, 32), (i * 5 % 255, 100, 150)).save(str(p))
        files.append(str(p))

    checker = PixelProbe(database_path=POSTGRES_URI, max_workers=4)
    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(lambda f: checker.scan_file(f, force_rescan=True), files))

        assert len(results) == 50
        assert all(r is not None for r in results)
        assert checker.failed_saves == 0, f'{checker.failed_saves} saves failed under concurrency'

        with engine.connect() as conn:
            count = conn.execute(
                text('SELECT count(*) FROM scan_results WHERE file_path LIKE :p'),
                {'p': f'{tmp_path}%'}
            ).scalar()
        assert count == 50, f'expected 50 persisted rows, got {count}'
    finally:
        with engine.begin() as conn:
            conn.execute(
                text('DELETE FROM scan_results WHERE file_path LIKE :p'),
                {'p': f'{tmp_path}%'}
            )
        engine.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_distinct_run_ids_have_one_global_postgres_reservation(tmp_path):
    """Concurrent UUIDs contend on the same PostgreSQL advisory slot."""
    schema = f"claim_{uuid4().hex[:12]}"
    engine = create_engine(POSTGRES_URI)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = POSTGRES_URI
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {'options': f'-csearch_path={schema}'}
    }
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()
    gate = Barrier(2)

    def reserve():
        with app.app_context():
            gate.wait()
            return claim_scan_slot(str(uuid4()), 'selected')[0]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = list(pool.map(lambda _: reserve(), range(2)))
        assert winners.count(True) == 1
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_selected_run_report_uses_immutable_membership(tmp_path):
    schema = f"selected_{uuid4().hex[:12]}"
    engine = create_engine(POSTGRES_URI)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI=POSTGRES_URI,
                      SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'options': f'-csearch_path={schema}'}},
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    image_path = tmp_path / 'valid.png'
    Image.new('RGB', (8, 8), 'blue').save(image_path)
    run_id = str(uuid4())
    try:
        with app.app_context():
            db.create_all()
            from pixelprobe.models import ScanConfiguration, ScanResult, ScanRunFile, ScanReport
            db.session.add(ScanConfiguration(path=str(tmp_path), is_active=True))
            db.session.add(ScanResult(file_path='/unrelated/bad.png', scan_status='completed', is_corrupted=True))
            db.session.commit()
            assert claim_scan_slot(run_id, 'selected')[0]
            scoped_uri = f"{POSTGRES_URI}?options={quote(f'-csearch_path={schema}')}"
            result = ScanService(scoped_uri).scan_files([str(image_path)], force_rescan=True,
                                                           num_workers=1, async_mode=False, scan_id=run_id)
            member = ScanRunFile.query.filter_by(scan_id=run_id).one()
            report = ScanReport.query.filter_by(scan_id=run_id).one()
            assert result['status'] == 'completed'
            assert member.status == 'completed'
            assert report.files_scanned == 1
            assert report.files_corrupted == 0
            original_hash = member.file_hash
            ScanResult.query.filter_by(id=member.scan_result_id).update({'file_hash': 'changed'})
            db.session.commit()
            assert ScanRunFile.query.filter_by(id=member.id).one().file_hash == original_hash
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()


@pytest.mark.postgres
@pytest.mark.timeout(300)
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_large_selected_scan_visits_each_multidirectory_file_once(tmp_path):
    schema = f"selected_large_{uuid4().hex[:12]}"
    engine = create_engine(POSTGRES_URI)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI=POSTGRES_URI,
                      SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'options': f'-csearch_path={schema}'}},
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    root = tmp_path / 'media'
    child = root / 'nested'
    child.mkdir(parents=True)
    paths = []
    for index in range(101):
        directory = root if index < 51 else child
        image_path = directory / f'image_{index}.png'
        Image.new('RGB', (8, 8), (index, 1, 2)).save(image_path)
        paths.append(str(image_path))
    run_id = str(uuid4())
    try:
        with app.app_context():
            db.create_all()
            from pixelprobe.models import ScanConfiguration, ScanChunk, ScanRunFile
            db.session.add(ScanConfiguration(path=str(root), is_active=True))
            db.session.commit()
            assert claim_scan_slot(run_id, 'selected')[0]
            scoped_uri = f"{POSTGRES_URI}?options={quote(f'-csearch_path={schema}')}"
            result = ScanService(scoped_uri).scan_files(paths, force_rescan=True,
                                                        num_workers=4, async_mode=False,
                                                        scan_id=run_id)
            members = ScanRunFile.query.filter_by(scan_id=run_id).all()
            chunks = ScanChunk.query.filter_by(scan_id=run_id).all()
            state = ScanState.query.filter_by(scan_id=run_id).one()
            assert result['status'] == 'completed'
            assert len(members) == len(paths)
            assert {member.file_path for member in members} == set(paths)
            assert {member.status for member in members} == {'completed'}
            assert len(chunks) == 2
            assert sum(chunk.files_scanned for chunk in chunks) == len(paths)
            assert {chunk.status for chunk in chunks} == {'completed'}
            assert state.files_processed == len(paths)
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()

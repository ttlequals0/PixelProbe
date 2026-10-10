import os
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from flask import Flask
from sqlalchemy import create_engine, event, literal, text
from sqlalchemy.pool import NullPool

from pixelprobe.models import (db as model_db, DuplicateDirtyKey,
                               DuplicateGroupSummary, DuplicateIndexState,
                               ScanResult)
from pixelprobe.services import duplicate_service
from pixelprobe.services.duplicate_service import (
    duplicate_index_status, duplicate_statistics, refresh_duplicate_index,
)


@pytest.fixture
def postgres_duplicates():
    uri = os.environ['PIXELPROBE_TEST_POSTGRES_URI']
    schema = f'dupidx_{uuid4().hex[:12]}'
    admin_engine = create_engine(uri)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))

    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=uri,
        SQLALCHEMY_ENGINE_OPTIONS={
            'connect_args': {'options': f'-csearch_path={schema}'},
        },
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    model_db.init_app(app)
    try:
        with app.app_context():
            model_db.create_all()
            from pixelprobe.migrations.duplicates import run_duplicate_index_migrations
            run_duplicate_index_migrations(model_db)
            yield app
    finally:
        with app.app_context():
            model_db.session.remove()
            model_db.engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin_engine.dispose()


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get('PIXELPROBE_TEST_POSTGRES_URI'),
        reason='PIXELPROBE_TEST_POSTGRES_URI not set',
    ),
]


def _scan(path, file_hash='hash-a', size=10, **fields):
    return ScanResult(file_path=path, file_hash=file_hash,
                      file_size=size, **fields)


def _dirty_keys():
    return set(model_db.session.query(
        DuplicateDirtyKey.mode, DuplicateDirtyKey.key_text,
        DuplicateDirtyKey.key_size,
    ).all())


def _summary_rows():
    return set(model_db.session.query(
        DuplicateGroupSummary.mode, DuplicateGroupSummary.key_text,
        DuplicateGroupSummary.key_size, DuplicateGroupSummary.group_size,
        DuplicateGroupSummary.group_id,
    ).all())


def _refresh_until_clean(limit=5):
    for _ in range(limit):
        assert refresh_duplicate_index()
        model_db.session.expire_all()
        if not _dirty_keys():
            return
    assert not _dirty_keys()


def _group_oracle(mode):
    groups, _ = duplicate_service._duplicate_groups(mode)
    return set(model_db.session.query(
        groups.c.key_0, groups.c.key_1 if mode == 'hash' else literal(-1),
        groups.c.group_size, groups.c.group_id,
    ).all())


def _assert_summary_matches_oracle():
    model_db.session.expire_all()
    actual = _summary_rows()
    expected = set()
    for mode in ('hash', 'name'):
        for key_text, key_size, group_size, group_id in _group_oracle(mode):
            expected.add((mode, key_text, key_size, group_size, group_id))
    assert actual == expected


def test_postgres_backfill_bulk_mutations_and_case_sensitive_names(postgres_duplicates):
    with postgres_duplicates.app_context():
        assert duplicate_index_status() == {
            'ready': False, 'updated_at': None, 'stale': False,
        }
        assert duplicate_statistics() == {
            'duplicate_files': None, 'duplicate_groups': None,
            'duplicate_extra_files': None, 'filename_duplicate_files': None,
            'filename_duplicate_groups': None,
            'filename_duplicate_extra_files': None,
        }
        model_db.session.add_all([
            _scan('/one/Photo.JPG'), _scan('/two/Photo.JPG'),
            _scan('/three/photo.jpg'),
        ])
        model_db.session.commit()

        assert refresh_duplicate_index()
        model_db.session.expire_all()
        assert duplicate_index_status()['ready'] is True
        assert _summary_rows() == {
            ('hash', 'hash-a', 10, 3, 1),
            ('name', 'Photo.JPG', -1, 2, 1),
        }
        _refresh_until_clean()

        model_db.session.add(_scan('/four/Photo.JPG', file_hash='hash-b', size=20))
        model_db.session.commit()
        _refresh_until_clean()
        _assert_summary_matches_oracle()
        assert ('name', 'Photo.JPG', -1, 3, 1) in _summary_rows()
        assert ('hash', 'hash-a', 10, 3, 1) in _summary_rows()

        model_db.session.query(ScanResult).filter_by(file_path='/one/Photo.JPG').update(
            {'file_hash': 'hash-b', 'file_size': 20}, synchronize_session=False)
        model_db.session.commit()
        _refresh_until_clean()
        _assert_summary_matches_oracle()
        assert ('hash', 'hash-a', 10, 2, 2) in _summary_rows()
        assert ('hash', 'hash-b', 20, 2, 1) in _summary_rows()

        model_db.session.query(ScanResult).filter_by(file_path='/two/Photo.JPG').delete(
            synchronize_session=False)
        model_db.session.commit()
        _refresh_until_clean()
        _assert_summary_matches_oracle()
        assert ('hash', 'hash-a', 10, 2, 3) not in _summary_rows()
        assert ('name', 'Photo.JPG', -1, 2, 1) in _summary_rows()

        from pixelprobe.migrations.duplicates import run_duplicate_index_migrations
        run_duplicate_index_migrations(model_db)
        assert refresh_duplicate_index()
        model_db.session.expire_all()
        _assert_summary_matches_oracle()


def test_postgres_trigger_tracks_eligibility_and_ignores_irrelevant_updates(postgres_duplicates):
    with postgres_duplicates.app_context():
        rows = [
            _scan('/a/shared.bin'), _scan('/b/shared.bin'),
            _scan('/c/Same.bin', file_hash='hash-c', size=11),
            _scan('/d/Same.bin', file_hash='hash-c', size=11),
        ]
        model_db.session.add_all(rows)
        model_db.session.commit()
        assert refresh_duplicate_index()
        _refresh_until_clean()
        model_db.session.expire_all()

        model_db.session.query(ScanResult).filter_by(file_path='/a/shared.bin').update(
            {'scan_status': 'completed'}, synchronize_session=False)
        model_db.session.query(ScanResult).filter_by(file_path='/b/shared.bin').update(
            {'scan_tool': 'scanner-v2'}, synchronize_session=False)
        model_db.session.query(ScanResult).filter_by(file_path='/c/Same.bin').update(
            {'file_path': '/elsewhere/Same.bin'}, synchronize_session=False)
        model_db.session.commit()
        assert _dirty_keys() == set()

        transitions = [
            ('bitrot_suspected', True), ('scan_status', 'failed'),
            ('scan_tool', 'error'), ('last_integrity_outcome', 'unreadable'),
            ('file_exists', False), ('file_hash', None), ('file_size', None),
        ]
        baseline = {
            'bitrot_suspected': False, 'scan_status': 'pending',
            'scan_tool': None, 'last_integrity_outcome': None,
            'file_exists': True, 'file_hash': 'hash-a', 'file_size': 10,
        }
        for field, invalid_value in transitions:
            model_db.session.query(ScanResult).filter_by(file_path='/a/shared.bin').update(
                baseline, synchronize_session=False)
            model_db.session.commit()
            _refresh_until_clean()
            _assert_summary_matches_oracle()
            model_db.session.query(ScanResult).filter_by(file_path='/a/shared.bin').update(
                {field: invalid_value}, synchronize_session=False)
            model_db.session.commit()
            assert ('hash', 'hash-a', 10) in _dirty_keys(), (
                f'eligibility change did not enqueue: {field}'
            )
            _refresh_until_clean()
            _assert_summary_matches_oracle()


def test_postgres_concurrent_insert_requeues_claimed_key(postgres_duplicates, monkeypatch):
    app = postgres_duplicates
    entered_refresh = threading.Event()
    allow_refresh = threading.Event()
    writer_started = threading.Event()
    writer_done = threading.Event()
    errors = []
    writer_pid = {}

    with app.app_context():
        model_db.session.add_all([
            _scan('/a/group.dat'), _scan('/b/group.dat'),
        ])
        model_db.session.commit()
        assert refresh_duplicate_index()
        _refresh_until_clean()
        model_db.session.add(_scan('/c/group.dat'))
        model_db.session.commit()
        model_db.session.expire_all()
        assert ('hash', 'hash-a', 10) in _dirty_keys()

    original = duplicate_service._refresh_claimed_keys

    def blocked_refresh(conn, keys, deadline):
        entered_refresh.set()
        if not allow_refresh.wait(5):
            raise AssertionError('test did not release the refresh worker')
        return original(conn, keys, deadline)

    monkeypatch.setattr(duplicate_service, '_refresh_claimed_keys', blocked_refresh)

    def refresh_worker():
        try:
            with app.app_context():
                calls = {'count': 0}

                def dispatch():
                    calls['count'] += 1
                    return calls['count'] <= 5

                refresh_duplicate_index(batch_size=1, can_dispatch=dispatch)
        except Exception as exc:
            errors.append(exc)

    def insert_worker():
        try:
            with app.app_context():
                connection = model_db.engine.connect()
                pid = connection.execute(text('SELECT pg_backend_pid()')).scalar_one()
                connection.commit()
                writer_pid['value'] = pid
                writer_started.set()
                connection.execute(text("""
                    INSERT INTO scan_results (file_path, file_hash, file_size,
                        file_exists, bitrot_suspected, marked_as_good, has_warnings)
                    VALUES ('/d/group.dat', 'hash-a', 10, TRUE, FALSE, FALSE, FALSE)
                """))
                connection.commit()
                connection.close()
                writer_done.set()
                errors.append(('writer_pid', pid))
        except Exception as exc:
            errors.append(exc)

    refresh_thread = threading.Thread(target=refresh_worker)
    refresh_thread.start()
    assert entered_refresh.wait(5)
    writer_thread = threading.Thread(target=insert_worker)
    writer_thread.start()
    assert writer_started.wait(5)

    blockers = []
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        worker_errors = [error for error in errors if isinstance(error, Exception)]
        if worker_errors:
            raise worker_errors[0]
        with app.app_context():
            connection = model_db.engine.connect()
            blockers = connection.execute(text(
                'SELECT pg_blocking_pids(:pid)'), {'pid': writer_pid['value']},
            ).scalar_one()
            connection.close()
            if blockers:
                break
        threading.Event().wait(0.02)
    if not blockers:
        with app.app_context():
            activity = model_db.session.execute(text("""
                SELECT wait_event_type, wait_event, state, query
                FROM pg_stat_activity WHERE pid = :pid
            """), {'pid': writer_pid['value']}).one_or_none()
        assert blockers, f'writer was not blocked; done={writer_done.is_set()}, activity={activity}'

    allow_refresh.set()
    refresh_thread.join(10)
    writer_thread.join(10)
    assert not refresh_thread.is_alive()
    assert not writer_thread.is_alive()
    assert not [error for error in errors if isinstance(error, Exception)]

    with app.app_context():
        model_db.session.expire_all()
        assert ('hash', 'hash-a', 10) in _dirty_keys()
        _refresh_until_clean()
        _assert_summary_matches_oracle()
        assert ('hash', 'hash-a', 10, 4, 1) in _summary_rows()


def test_postgres_incremental_failure_rolls_back_summary_and_retries(postgres_duplicates, monkeypatch):
    with postgres_duplicates.app_context():
        model_db.session.add_all([_scan('/a/fail.dat'), _scan('/b/fail.dat')])
        model_db.session.commit()
        assert refresh_duplicate_index()
        _refresh_until_clean()
        baseline = _summary_rows()

        model_db.session.query(ScanResult).filter_by(file_path='/a/fail.dat').update(
            {'file_size': 99}, synchronize_session=False)
        model_db.session.commit()
        model_db.session.expire_all()
        pending = _dirty_keys()
        assert pending
        original = duplicate_service._refresh_claimed_keys

        def fail_after_writes(conn, keys, deadline):
            original(conn, keys, deadline)
            raise RuntimeError('forced transaction rollback')

        monkeypatch.setattr(duplicate_service, '_refresh_claimed_keys', fail_after_writes)
        with pytest.raises(RuntimeError, match='forced transaction rollback'):
            refresh_duplicate_index()
        model_db.session.expire_all()
        assert _summary_rows() == baseline
        assert _dirty_keys() == pending
        assert duplicate_index_status()['stale'] is True

        monkeypatch.setattr(duplicate_service, '_refresh_claimed_keys', original)
        _refresh_until_clean()
        _assert_summary_matches_oracle()
        assert duplicate_index_status()['stale'] is False


def test_postgres_backfill_failure_is_retryable_and_migration_is_idempotent(
        postgres_duplicates, monkeypatch):
    with postgres_duplicates.app_context():
        model_db.session.add_all([_scan('/a/retry.dat'), _scan('/b/retry.dat')])
        model_db.session.commit()
        from pixelprobe.migrations.duplicates import run_duplicate_index_migrations
        run_duplicate_index_migrations(model_db)
        model_db.session.expire_all()
        assert duplicate_index_status()['ready'] is False
        original = duplicate_service._initial_grouping

        def fail_after_backfill(conn):
            original(conn)
            raise RuntimeError('forced backfill failure')

        monkeypatch.setattr(duplicate_service, '_initial_grouping', fail_after_backfill)
        with pytest.raises(RuntimeError, match='forced backfill failure'):
            refresh_duplicate_index()
        model_db.session.expire_all()
        state = model_db.session.get(DuplicateIndexState, 1)
        assert state.ready is False
        assert _summary_rows() == set()

        monkeypatch.setattr(duplicate_service, '_initial_grouping', original)
        assert refresh_duplicate_index()
        model_db.session.expire_all()
        assert duplicate_index_status()['ready'] is True
        _refresh_until_clean()
        _assert_summary_matches_oracle()
        run_duplicate_index_migrations(model_db)
        assert refresh_duplicate_index()
        model_db.session.expire_all()
        _assert_summary_matches_oracle()


def test_postgres_refresh_failures_release_advisory_lock(postgres_duplicates, monkeypatch):
    lock_id = duplicate_service._DUPLICATE_REFRESH_LOCK_ID

    def assert_lock_free():
        verifier_engine = create_engine(
            os.environ['PIXELPROBE_TEST_POSTGRES_URI'], poolclass=NullPool,
        )
        try:
            connection = verifier_engine.connect()
            acquired = connection.execute(text(
                'SELECT pg_try_advisory_lock(:lock_id)'), {'lock_id': lock_id},
            ).scalar_one()
            assert acquired is True
            connection.execute(text(
                'SELECT pg_advisory_unlock(:lock_id)'), {'lock_id': lock_id},
            )
            connection.commit()
            connection.close()
        finally:
            verifier_engine.dispose()

    with postgres_duplicates.app_context():
        original_ensure = duplicate_service._ensure_duplicate_indexes
        monkeypatch.setattr(duplicate_service, '_ensure_duplicate_indexes',
                            lambda can_dispatch=None: (_ for _ in ()).throw(
                                RuntimeError('index prep failed')))
        with pytest.raises(RuntimeError, match='index prep failed'):
            refresh_duplicate_index()
        assert_lock_free()

        monkeypatch.setattr(duplicate_service, '_ensure_duplicate_indexes', original_ensure)
        assert refresh_duplicate_index(can_dispatch=lambda: False) is False
        assert_lock_free()

        model_db.session.add_all([_scan('/a/sql-fail.dat'), _scan('/b/sql-fail.dat')])
        model_db.session.commit()
        assert refresh_duplicate_index()
        _refresh_until_clean()
        original_grouping = duplicate_service._initial_grouping

        def sql_failure(conn):
            conn.execute(text('SELECT 1 / 0'))

        monkeypatch.setattr(duplicate_service, '_initial_grouping', sql_failure)
        model_db.session.query(DuplicateIndexState).filter_by(id=1).update(
            {'ready': False}, synchronize_session=False)
        model_db.session.commit()
        with pytest.raises(Exception):
            refresh_duplicate_index()
        assert_lock_free()
        monkeypatch.setattr(duplicate_service, '_initial_grouping', original_grouping)


def test_postgres_index_preparation_resets_statement_timeout(postgres_duplicates):
    with postgres_duplicates.app_context():
        observed_timeout = {}

        def fail_index_creation(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('CREATE INDEX CONCURRENTLY'):
                raise RuntimeError('forced concurrent index failure')

        def capture_reset(connection, cursor, statement, parameters, context, executemany):
            if statement.strip().upper() == 'RESET STATEMENT_TIMEOUT':
                cursor.execute('SHOW statement_timeout')
                observed_timeout['value'] = cursor.fetchone()[0]

        event.listen(model_db.engine, 'before_cursor_execute', fail_index_creation)
        event.listen(model_db.engine, 'after_cursor_execute', capture_reset)
        try:
            with pytest.raises(RuntimeError, match='forced concurrent index failure'):
                duplicate_service._ensure_duplicate_indexes()
        finally:
            event.remove(model_db.engine, 'before_cursor_execute', fail_index_creation)
            event.remove(model_db.engine, 'after_cursor_execute', capture_reset)
        assert observed_timeout.get('value') == '0'
        assert duplicate_service._ensure_duplicate_indexes() is True


def test_postgres_stale_status_uses_queue_age_or_refresh_failure(postgres_duplicates):
    with postgres_duplicates.app_context():
        model_db.session.add_all([_scan('/a/stale.dat'), _scan('/b/stale.dat')])
        model_db.session.commit()
        assert refresh_duplicate_index()
        _refresh_until_clean()
        model_db.session.add(DuplicateDirtyKey(
            mode='hash', key_text='hash-a', key_size=10,
            enqueued_at=datetime.now(timezone.utc) - timedelta(seconds=61),
        ))
        model_db.session.commit()
        assert duplicate_index_status()['stale'] is True
        model_db.session.query(DuplicateDirtyKey).delete()
        model_db.session.query(DuplicateIndexState).filter_by(id=1).update(
            {'refresh_failed': True}, synchronize_session=False)
        model_db.session.commit()
        assert duplicate_index_status()['stale'] is True
        model_db.session.query(DuplicateIndexState).filter_by(id=1).update(
            {'refresh_failed': False}, synchronize_session=False)
        model_db.session.add(DuplicateDirtyKey(
            mode='hash', key_text='hash-a', key_size=10,
            enqueued_at=datetime.now(timezone.utc),
        ))
        model_db.session.commit()
        assert duplicate_index_status()['stale'] is False

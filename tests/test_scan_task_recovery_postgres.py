"""PostgreSQL regressions for durable scan task dispatch intents."""

import os
import sys
import types
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

import pytest
from flask import Flask
from sqlalchemy import create_engine, text

from pixelprobe.models import db, ScanChunk, ScanConfiguration, ScanResult, ScanRunFile, ScanState, ScanTask
from pixelprobe.services.scan_reporting import add_files_batch_to_db


POSTGRES_URI = os.environ.get('PIXELPROBE_TEST_POSTGRES_URI')


@pytest.fixture
def recovery_app(monkeypatch):
    schema = f'task_recovery_{uuid4().hex[:12]}'
    engine = create_engine(POSTGRES_URI)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=POSTGRES_URI,
        SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'options': f'-csearch_path={schema}'}},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TEST_SCHEMA=schema,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()

    original = sys.modules.get('app')
    original_task_module = sys.modules.get('pixelprobe.tasks_parallel')
    original_celery_module = sys.modules.get('pixelprobe.celery_config')
    fake = types.ModuleType('app')
    fake.app = app
    monkeypatch.setitem(sys.modules, 'app', fake)
    import pixelprobe.tasks_parallel as task_module
    try:
        yield app, task_module
    finally:
        with app.app_context():
            db.session.remove()
        if original is None:
            sys.modules.pop('app', None)
        else:
            sys.modules['app'] = original
        sys.modules.pop('pixelprobe.tasks_parallel', None)
        sys.modules.pop('pixelprobe.celery_config', None)
        if original_task_module is not None:
            sys.modules['pixelprobe.tasks_parallel'] = original_task_module
        if original_celery_module is not None:
            sys.modules['pixelprobe.celery_config'] = original_celery_module
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()


def _queued_chunk_task():
    state = ScanState(scan_id='recovery-run', phase='scanning', is_active=True)
    db.session.add(state)
    db.session.flush()
    chunk = ScanChunk(
        scan_id=state.scan_id,
        chunk_id='recovery-chunk', directory_path='{"t":"FCP","f":"/a","l":"/z"}',
        phase='scanning', status='pending', files_discovered=1,
    )
    db.session.add(chunk)
    db.session.flush()
    task = ScanTask(
        scan_id=state.scan_id, chunk_id=chunk.id, purpose='chunk',
        celery_task_id='recovery-task', generation=state.dispatch_generation,
        payload={'force_rescan': False}, status='queued',
    )
    chunk.celery_task_id = task.celery_task_id
    db.session.add(task)
    db.session.commit()
    return task.id


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_concurrent_reconcilers_publish_one_active_intent(recovery_app):
    app, task_module = recovery_app
    with app.app_context():
        task_id = _queued_chunk_task()
    barrier = Barrier(2)

    def reconcile():
        with app.app_context():
            barrier.wait()
            return task_module.reconcile_scan_task_intents()

    with patch.object(task_module.process_chunk_task, 'apply_async') as publish:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: reconcile(), range(2)))

    assert sum(outcomes) == 1
    assert publish.call_count == 1
    with app.app_context():
        task = db.session.get(ScanTask, task_id)
        assert task.status == 'dispatched'
        assert task.dispatch_attempts == 1


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_publish_failure_remains_recoverable_and_cancelled_run_never_republishes(recovery_app):
    app, task_module = recovery_app
    with app.app_context():
        task_id = _queued_chunk_task()
        with patch.object(task_module.process_chunk_task, 'apply_async',
                          side_effect=RuntimeError('broker unavailable')):
            assert task_module.reconcile_scan_task_intents() == 0
        task = db.session.get(ScanTask, task_id)
        assert task.status == 'queued'
        assert 'broker unavailable' in task.error_message

        state = ScanState.query.filter_by(scan_id='recovery-run').one()
        state.phase = 'cancelled'
        state.is_active = False
        db.session.commit()
        with patch.object(task_module.process_chunk_task, 'apply_async') as publish:
            assert task_module.reconcile_scan_task_intents() == 0
        assert db.session.get(ScanTask, task_id).status == 'cancelled'
        publish.assert_not_called()


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_concurrent_worker_delivery_claims_one_owned_intent(recovery_app):
    app, task_module = recovery_app
    with app.app_context():
        _queued_chunk_task()
    barrier = Barrier(2)

    def claim():
        with app.app_context():
            barrier.wait()
            return task_module._mark_task_running('recovery-run', 'recovery-task')

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))

    assert results.count(True) == 1
    assert results.count(False) == 1
    with app.app_context():
        assert ScanTask.query.filter_by(celery_task_id='recovery-task').one().status == 'processing'


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_stale_processing_orchestrator_and_continuation_return_to_dispatch(recovery_app):
    app, task_module = recovery_app
    with app.app_context():
        state = ScanState(scan_id='continuation-run', phase='adding', is_active=True)
        db.session.add(state)
        db.session.flush()
        continuation = ScanTask(
            scan_id=state.scan_id, purpose='continuation', celery_task_id='continuation-task',
            generation=state.dispatch_generation, payload={'force_rescan': False},
            status='processing',
        )
        orchestrator = ScanTask(
            scan_id=state.scan_id, purpose='orchestrator', celery_task_id='orchestrator-task',
            generation=state.dispatch_generation,
            payload={'scan_id': state.scan_id, 'paths': [], 'scan_type': 'pending'},
            status='processing',
        )
        db.session.add_all([continuation, orchestrator])
        db.session.commit()
        assert task_module.recover_stale_processing_task_intents(state.scan_id) == 2
        assert db.session.get(ScanTask, continuation.id).status == 'queued'
        assert db.session.get(ScanTask, orchestrator.id).status == 'queued'
        with patch.object(task_module.resume_scan_after_discovery, 'apply_async') as continuation_publish, \
             patch.object(task_module.parallel_scan_orchestrator, 'apply_async') as orchestrator_publish:
            assert task_module.reconcile_scan_task_intents() == 2
        assert db.session.get(ScanTask, continuation.id).status == 'dispatched'
        assert db.session.get(ScanTask, orchestrator.id).status == 'dispatched'
        continuation_publish.assert_called_once()
        orchestrator_publish.assert_called_once()


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_cancellation_marks_only_its_dispatched_and_processing_intents(recovery_app):
    app, _ = recovery_app
    with app.app_context():
        from pixelprobe.services.scan_service import ScanService

        owned = ScanState(scan_id='owned-run', phase='scanning', is_active=True)
        unrelated = ScanState(scan_id='other-run', phase='scanning', is_active=True)
        db.session.add_all([owned, unrelated])
        db.session.flush()
        db.session.add_all([
            ScanTask(scan_id='owned-run', purpose='chunk', celery_task_id='owned-dispatched',
                     generation=owned.dispatch_generation, status='dispatched'),
            ScanTask(scan_id='owned-run', purpose='chunk', celery_task_id='owned-processing',
                     generation=owned.dispatch_generation, status='processing'),
            ScanTask(scan_id='other-run', purpose='chunk', celery_task_id='other-processing',
                     generation=unrelated.dispatch_generation, status='processing'),
        ])
        db.session.commit()

        with patch('pixelprobe.celery_config.celery_app.control.revoke') as revoke:
            result = ScanService(POSTGRES_URI).cancel_scan(expected_scan_id='owned-run')

        assert result['owned_task_count'] == 2
        assert result['revoked_task_count'] == 2
        assert {call.args[0] for call in revoke.call_args_list} == {
            'owned-dispatched', 'owned-processing'}
        assert ScanTask.query.filter_by(celery_task_id='owned-dispatched').one().status == 'cancelled'
        assert ScanTask.query.filter_by(celery_task_id='owned-processing').one().status == 'cancelled'
        assert ScanTask.query.filter_by(celery_task_id='other-processing').one().status == 'processing'


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_concurrent_discovery_membership_insert_is_idempotent(recovery_app, tmp_path):
    app, _ = recovery_app
    path = tmp_path / 'overlap.mkv'
    path.write_bytes(b'media')
    with app.app_context():
        db.session.add(ScanResult(file_path=str(path), scan_status='pending'))
        db.session.commit()
    barrier = Barrier(2)

    def add_member():
        with app.app_context():
            barrier.wait()
            return add_files_batch_to_db([str(path)], scan_id='overlap-run')

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: add_member(), range(2)))
    with app.app_context():
        assert ScanRunFile.query.filter_by(scan_id='overlap-run', file_path=str(path)).count() == 1


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_cancelled_decode_cannot_overwrite_newer_result(recovery_app, tmp_path):
    app, task_module = recovery_app
    file_path = str(tmp_path / 'late-result.mkv')
    tmp_path.joinpath('late-result.mkv').write_bytes(b'media')
    started = Event()
    release = Event()
    observed = {
        'outcome': 'completed', 'is_corrupted': False, 'file_hash': 'old-hash',
        'last_modified': '2026-01-01T00:00:00+00:00', 'scan_output': 'old worker',
        'scan_tool': 'test', 'scan_duration': 1, 'file_size': 1, 'file_type': 'video',
    }
    with app.app_context():
        db.session.add(ScanConfiguration(path=str(tmp_path), is_active=True))
        state = ScanState(scan_id='late-run', phase='scanning', is_active=True)
        result = ScanResult(file_path=file_path, scan_status='pending', file_hash='original')
        db.session.add_all([state, result])
        db.session.flush()
        member = ScanRunFile(scan_id=state.scan_id, scan_result_id=result.id,
                             file_path=file_path, status='pending')
        chunk = ScanChunk(scan_id=state.scan_id, chunk_id='late-chunk',
                          directory_path='{"t":"FCP","f":"' + file_path + '","l":"' + file_path + '"}',
                          phase='scanning', status='pending', files_discovered=1)
        db.session.add_all([member, chunk])
        db.session.commit()
        chunk_id = chunk.id

    def blocked_decode(*_args, **_kwargs):
        started.set()
        assert release.wait(10)
        return observed

    def run_old_worker():
        with app.app_context():
            return task_module.process_chunk_task.run(chunk_id, 'late-run')

    with patch.object(task_module.PixelProbe, 'scan_file', side_effect=blocked_decode):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_old_worker)
            if not started.wait(10):
                raise AssertionError(f'worker exited before decode: {future.result(timeout=2)}')
            with app.app_context():
                state = ScanState.query.filter_by(scan_id='late-run').with_for_update().one()
                state.is_active = False
                state.phase = 'cancelled'
                result = ScanResult.query.filter_by(file_path=file_path).one()
                result.file_hash = 'newer-hash'
                result.scan_status = 'completed'
                ScanRunFile.query.filter_by(scan_id='late-run').update(
                    {'status': 'pending'}, synchronize_session=False)
                db.session.commit()
            release.set()
            outcome = future.result(timeout=15)

    assert outcome['status'] == 'CANCELLED'
    with app.app_context():
        result = ScanResult.query.filter_by(file_path=file_path).one()
        member = ScanRunFile.query.filter_by(scan_id='late-run', file_path=file_path).one()
        assert result.file_hash == 'newer-hash'
        assert member.status == 'pending'


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_selected_cache_guard_rejects_cancelled_private_session_write(recovery_app, tmp_path):
    app, _ = recovery_app
    file_path = str(tmp_path / 'selected-cache.mkv')
    with app.app_context():
        state = ScanState(scan_id='selected-cache-run', phase='cancelled', is_active=False)
        result = ScanResult(file_path=file_path, scan_status='completed', file_hash='newer-hash')
        db.session.add_all([state, result])
        db.session.flush()
        db.session.add(ScanRunFile(scan_id=state.scan_id, scan_result_id=result.id,
                                   file_path=file_path, status='pending'))
        db.session.commit()
        from pixelprobe.media_checker import PixelProbe
        from pixelprobe.services.scan_service import ScanService
        schema_uri = f'{POSTGRES_URI}?options={quote("-csearch_path=" + app.config["TEST_SCHEMA"])}'
        checker = PixelProbe(
            database_path=schema_uri,
            result_persistence_guard=ScanService(POSTGRES_URI)._selected_result_persistence_guard(
                state.scan_id),
        )
        try:
            checker._save_to_cache(file_path, {
                'outcome': 'completed', 'file_hash': 'old-hash',
                'last_modified': '2026-01-01T00:00:00+00:00', 'is_corrupted': False,
            })
        finally:
            checker.dispose_database_connection()
        row = ScanResult.query.filter_by(file_path=file_path).one()
        assert row.file_hash == 'newer-hash'

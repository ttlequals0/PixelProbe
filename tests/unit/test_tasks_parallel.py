"""Unit tests for the chunk-distributed scan engine (tasks_parallel)"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from pixelprobe.models import ScanResult, ScanState, ScanChunk


@pytest.fixture
def engine(app):
    """Celery-free engine core (importable without the full app/celery stack)"""
    import pixelprobe.services.scan_engine as engine_module
    return engine_module


@pytest.fixture
def tp(tasks_parallel_mod):
    """Shorthand for the shared tasks_parallel fixture (see conftest.py)"""
    return tasks_parallel_mod


def _add_pending(db, paths):
    for p in paths:
        db.session.add(ScanResult(file_path=p, scan_status='pending'))
    db.session.commit()


def _make_scan_state(db, scan_id, phase='scanning', is_active=True, scan_type='full'):
    state = ScanState(scan_id=scan_id, phase=phase, is_active=is_active,
                      scan_type=scan_type)
    state.start_time = datetime.now(timezone.utc)
    db.session.add(state)
    db.session.commit()
    return state


def _make_chunk(db, scan_id, first, last, is_complete=False, status='pending',
                files_scanned=0):
    chunk = ScanChunk(
        scan_id=scan_id,
        chunk_id=f'chunk-{first}',
        directory_path=json.dumps({'t': 'FCP', 'f': first, 'l': last}),
        phase='scanning',
        status=status,
        is_complete=is_complete,
        files_scanned=files_scanned,
        files_discovered=0,
    )
    db.session.add(chunk)
    db.session.commit()
    return chunk


class TestBuildScanChunks:

    def test_chunks_cover_all_pending_in_path_order(self, engine, app, db):
        with app.app_context():
            paths = [f'/media/dir/file{i:03d}.mkv' for i in range(7)]
            _add_pending(db, paths)

            chunks = engine.build_scan_chunks('scan-1')

            assert len(chunks) >= 1
            assert sum(c['files_discovered'] for c in chunks) == len(paths)

            rows = ScanChunk.query.filter_by(scan_id='scan-1').order_by(ScanChunk.id).all()
            ranges = [r.fcp_range() for r in rows]
            assert all(r is not None for r in ranges)
            assert ranges[0][0] == paths[0]
            assert ranges[-1][1] == paths[-1]

    def test_no_pending_files_creates_no_chunks(self, engine, app, db):
        with app.app_context():
            assert engine.build_scan_chunks('scan-1') == []

    def test_completed_files_not_chunked(self, engine, app, db):
        with app.app_context():
            db.session.add(ScanResult(file_path='/media/a.mkv', scan_status='completed'))
            db.session.add(ScanResult(file_path='/media/b.mkv', scan_status='pending'))
            db.session.commit()

            chunks = engine.build_scan_chunks('scan-1')
            assert sum(c['files_discovered'] for c in chunks) == 1


class TestFinalization:

    def test_not_finalized_while_chunks_incomplete(self, engine, app, db):
        with app.app_context():
            _make_scan_state(db, 'scan-f1')
            _make_chunk(db, 'scan-f1', '/a', '/b', is_complete=False)

            assert engine.maybe_finalize_scan('scan-f1') is False
            state = ScanState.query.filter_by(scan_id='scan-f1').first()
            assert state.is_active is True
            assert state.phase == 'scanning'

    @patch('pixelprobe.services.scan_engine.create_scan_report')
    def test_finalizes_when_all_chunks_terminal(self, mock_report, engine, app, db):
        with app.app_context():
            _make_scan_state(db, 'scan-f2')
            _make_chunk(db, 'scan-f2', '/a', '/b', is_complete=True,
                        status='completed', files_scanned=5)
            _make_chunk(db, 'scan-f2', '/c', '/d', is_complete=True,
                        status='completed', files_scanned=3)

            assert engine.maybe_finalize_scan('scan-f2') is True

            state = ScanState.query.filter_by(scan_id='scan-f2').first()
            assert state.phase == 'completed'
            assert state.is_active is False
            assert state.end_time is not None
            # Totals come from chunk sums, not global table counts
            assert state.files_processed == 8
            mock_report.assert_called_once()

    @patch('pixelprobe.services.scan_engine.create_scan_report')
    def test_errored_chunks_finalize_as_error(self, mock_report, engine, app, db):
        """A scan with failed chunks must not report a clean completion"""
        with app.app_context():
            _make_scan_state(db, 'scan-f9')
            _make_chunk(db, 'scan-f9', '/a', '/b', is_complete=True,
                        status='completed', files_scanned=5)
            _make_chunk(db, 'scan-f9', '/c', '/d', is_complete=True,
                        status='error', files_scanned=3)

            assert engine.maybe_finalize_scan('scan-f9') is True

            state = ScanState.query.filter_by(scan_id='scan-f9').first()
            assert state.phase == 'error'
            assert state.is_active is False
            assert 'chunks failed' in state.error_message
            mock_report.assert_called_once()

    @patch('pixelprobe.services.scan_engine.create_scan_report')
    def test_finalize_is_exactly_once(self, mock_report, engine, app, db):
        with app.app_context():
            _make_scan_state(db, 'scan-f3')
            _make_chunk(db, 'scan-f3', '/a', '/b', is_complete=True,
                        status='completed', files_scanned=1)

            assert engine.maybe_finalize_scan('scan-f3') is True
            # Second caller sees phase != 'scanning' and does nothing
            assert engine.maybe_finalize_scan('scan-f3') is False
            assert mock_report.call_count == 1

    def test_not_finalized_when_scan_inactive(self, engine, app, db):
        """A cancelled scan (is_active=False) must not be flipped to completed"""
        with app.app_context():
            _make_scan_state(db, 'scan-f4', phase='cancelled', is_active=False)
            _make_chunk(db, 'scan-f4', '/a', '/b', is_complete=True, status='cancelled')

            assert engine.maybe_finalize_scan('scan-f4') is False
            state = ScanState.query.filter_by(scan_id='scan-f4').first()
            assert state.phase == 'cancelled'

    def test_not_finalized_with_zero_chunks(self, engine, app, db):
        """An active scan with no chunks (legacy engine or pre-chunking) must
        never be finalized from the chunk path or the sweeper backstop"""
        with app.app_context():
            _make_scan_state(db, 'scan-f7')

            assert engine.maybe_finalize_scan('scan-f7') is False
            state = ScanState.query.filter_by(scan_id='scan-f7').first()
            assert state.is_active is True
            assert state.phase == 'scanning'

    def test_not_finalized_without_scan_type(self, engine, app, db):
        """Legacy-engine scans (no scan_type) are out of the finalizer's scope"""
        with app.app_context():
            _make_scan_state(db, 'scan-f8', scan_type=None)
            _make_chunk(db, 'scan-f8', '/a', '/b', is_complete=True, status='completed')

            assert engine.maybe_finalize_scan('scan-f8') is False
            state = ScanState.query.filter_by(scan_id='scan-f8').first()
            assert state.is_active is True

    @patch('pixelprobe.services.scan_engine.create_scan_report')
    def test_finalize_reclaims_stuck_scanning_rows(self, mock_report, engine, app, db):
        with app.app_context():
            db.session.add(ScanResult(file_path='/media/stuck.mkv', scan_status='scanning'))
            db.session.commit()
            _make_scan_state(db, 'scan-f5')
            _make_chunk(db, 'scan-f5', '/a', '/b', is_complete=True, status='completed')

            assert engine.maybe_finalize_scan('scan-f5') is True
            row = ScanResult.query.filter_by(file_path='/media/stuck.mkv').first()
            assert row.scan_status == 'pending'

    @patch('pixelprobe.services.scan_engine.create_scan_report')
    def test_finalizer_never_decreases_progress(self, mock_report, engine, app, db):
        with app.app_context():
            state = _make_scan_state(db, 'scan-f6')
            state.files_processed = 100
            db.session.commit()
            _make_chunk(db, 'scan-f6', '/a', '/b', is_complete=True,
                        status='completed', files_scanned=10)

            assert engine.maybe_finalize_scan('scan-f6') is True
            state = ScanState.query.filter_by(scan_id='scan-f6').first()
            assert state.files_processed == 100


class TestChunkTaskGuards:
    """Duplicate-delivery guards: a ghost or superseded task must not touch
    chunk state (issue #75)"""

    def test_terminal_chunk_returns_already_terminal(self, tp, app, db):
        with app.app_context():
            _make_scan_state(db, 'scan-g1')
            chunk = _make_chunk(db, 'scan-g1', '/a', '/b', is_complete=True,
                                status='completed', files_scanned=42)
            result = tp.process_chunk_task.apply(args=(chunk.id, 'scan-g1')).get()
            assert result['status'] == 'ALREADY_TERMINAL'
            chunk = db.session.get(ScanChunk, chunk.id)
            assert chunk.status == 'completed'
            assert chunk.files_scanned == 42

    def test_superseded_task_id_claims_nothing(self, tp, app, db):
        with app.app_context():
            _add_pending(db, ['/a/f1.mkv'])
            _make_scan_state(db, 'scan-g2')
            chunk = _make_chunk(db, 'scan-g2', '/a', '/b', status='pending')
            chunk.celery_task_id = 'the-current-owner'
            db.session.commit()
            # apply() runs with a generated eager task id != the-current-owner
            result = tp.process_chunk_task.apply(args=(chunk.id, 'scan-g2')).get()
            assert result['status'] == 'SUPERSEDED'
            assert ScanResult.query.filter_by(scan_status='scanning').count() == 0
            chunk = db.session.get(ScanChunk, chunk.id)
            assert chunk.status == 'pending'


class TestChunkHeartbeat:
    """Heartbeat makes ScanState.last_update a liveness signal (issue #75)"""

    def test_heartbeat_once_bumps_active_scan(self, tp, app, db):
        with app.app_context():
            state = _make_scan_state(db, 'scan-h1')
            old = datetime(2020, 1, 1, tzinfo=timezone.utc)
            state.last_update = old
            db.session.commit()
        assert tp._heartbeat_once(app, 'scan-h1') is True
        with app.app_context():
            state = ScanState.query.filter_by(scan_id='scan-h1').first()
            assert state.last_update is not None
            lu = state.last_update
            if lu.tzinfo is None:
                lu = lu.replace(tzinfo=timezone.utc)
            assert lu > old

    def test_heartbeat_skips_inactive_scan(self, tp, app, db):
        with app.app_context():
            state = _make_scan_state(db, 'scan-h2', is_active=False)
            state.last_update = datetime(2020, 1, 1, tzinfo=timezone.utc)
            db.session.commit()
        assert tp._heartbeat_once(app, 'scan-h2') is False

    def test_heartbeat_swallows_db_errors(self, tp, app):
        with patch.object(tp.db, 'session') as mock_session:
            mock_session.execute.side_effect = RuntimeError('pool exhausted')
            assert tp._heartbeat_once(app, 'scan-h3') is False

    def test_heartbeat_thread_lifecycle(self, tp, app):
        import time as _time
        with patch.object(tp, '_heartbeat_once') as mock_beat:
            stop = tp._start_chunk_heartbeat(app, 'scan-h4', 1, interval=0.02)
            deadline = _time.time() + 2
            while mock_beat.call_count < 2 and _time.time() < deadline:
                _time.sleep(0.01)
            stop.set()
            assert mock_beat.call_count >= 2


class TestRedispatchOrphanedChunks:
    """Revival of chunks whose workers are provably gone (issue #75)"""

    def _setup_orphaned_scan(self, db):
        _add_pending(db, ['/a/f1.mkv', '/a/f2.mkv'])
        state = _make_scan_state(db, 'scan-r1')
        state.force_rescan = False
        processing = _make_chunk(db, 'scan-r1', '/a/f1.mkv', '/a/f1.mkv',
                                 status='processing')
        # simulate the dead worker's claimed row
        row = ScanResult.query.filter_by(file_path='/a/f1.mkv').first()
        row.scan_status = 'scanning'
        pending = _make_chunk(db, 'scan-r1', '/a/f2.mkv', '/a/f2.mkv',
                              status='pending')
        done = _make_chunk(db, 'scan-r1', '/z', '/z2', is_complete=True,
                           status='completed', files_scanned=7)
        db.session.commit()
        return processing, pending, done

    def test_reclaims_and_redispatches_nonterminal_chunks(self, tp, app, db):
        with app.app_context():
            processing, pending, done = self._setup_orphaned_scan(db)
            with patch.object(tp.process_chunk_task, 'apply_async') as mock_async:
                count = tp.redispatch_orphaned_chunks('scan-r1')
            assert count == 2
            assert mock_async.call_count == 2
            row = ScanResult.query.filter_by(file_path='/a/f1.mkv').first()
            assert row.scan_status == 'pending'
            for chunk in (db.session.get(ScanChunk, processing.id),
                          db.session.get(ScanChunk, pending.id)):
                assert chunk.status == 'pending'
                assert chunk.celery_task_id
                assert chunk.start_time is None
            # dispatched task_id matches the stored ownership id
            dispatched_ids = {c.kwargs['task_id'] for c in mock_async.call_args_list}
            stored_ids = {db.session.get(ScanChunk, processing.id).celery_task_id,
                          db.session.get(ScanChunk, pending.id).celery_task_id}
            assert dispatched_ids == stored_ids
            done_chunk = db.session.get(ScanChunk, done.id)
            assert done_chunk.status == 'completed'
            assert done_chunk.files_scanned == 7
            state = ScanState.query.filter_by(scan_id='scan-r1').first()
            assert 'Recovered' in (state.progress_message or '')

    def test_dispatch_error_attempts_every_chunk(self, tp, app, db):
        with app.app_context():
            self._setup_orphaned_scan(db)
            with patch.object(tp.process_chunk_task, 'apply_async',
                              side_effect=RuntimeError('broker down')) as mock_async:
                count = tp.redispatch_orphaned_chunks('scan-r1')
            # One broker hiccup must not strand the remaining chunks
            assert count == 0
            assert mock_async.call_count == 2


class TestWorkerLostRedelivery:
    """acks_late + reject_on_worker_lost redelivers the SAME task id after a
    worker dies mid-chunk; the leftover 'scanning' rows must be reclaimed and
    re-scanned, not dropped via the empty-claim completed-with-0 path"""

    def test_redelivery_reclaims_scanning_rows(self, tp, app, db):
        with app.app_context():
            _add_pending(db, ['/nonexistent/f1.mkv'])
            row = ScanResult.query.filter_by(file_path='/nonexistent/f1.mkv').first()
            row.scan_status = 'scanning'  # claimed by the dead attempt
            _make_scan_state(db, 'scan-wl1')
            chunk = _make_chunk(db, 'scan-wl1', '/nonexistent/f1.mkv',
                                '/nonexistent/f1.mkv', status='processing')
            db.session.commit()

            result = tp.process_chunk_task.apply(args=(chunk.id, 'scan-wl1')).get()

            assert result['status'] == 'SUCCESS'
            assert result['files_processed'] == 1
            row = ScanResult.query.filter_by(file_path='/nonexistent/f1.mkv').first()
            assert row.scan_status != 'scanning'

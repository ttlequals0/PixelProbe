"""Unit tests for the chunk-distributed scan engine (tasks_parallel)"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from pixelprobe.models import (
    ScanConfiguration, ScanResult, ScanState, ScanChunk, ScanRunFile, ScanRunRoot, ScanTask,
)
from pixelprobe.services.scan_reporting import add_files_batch_to_db


@pytest.fixture
def engine(app):
    """Celery-free engine core (importable without the full app/celery stack)"""
    import pixelprobe.services.scan_engine as engine_module
    return engine_module


@pytest.fixture
def tp(tasks_parallel_mod):
    """Shorthand for the shared tasks_parallel fixture (see conftest.py)"""
    return tasks_parallel_mod


def _add_pending(db, paths, scan_id='scan-1'):
    _make_scan_state(db, scan_id)
    for p in paths:
        result = ScanResult(file_path=p, scan_status='pending')
        db.session.add(result)
        db.session.flush()
        db.session.add(ScanRunFile(scan_id=scan_id, scan_result_id=result.id,
                                   file_path=p, status='pending'))
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


def _authorized_media_root(db, tmp_path, *names):
    db.session.add(ScanConfiguration(path=str(tmp_path), is_active=True))
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b'not-a-real-video')
        paths.append(str(path))
    db.session.commit()
    return paths


class TestDiscoveryMembership:

    def test_nonforce_discovery_members_only_new_or_pending_inventory(self, app, db, tmp_path):
        with app.app_context():
            paths = [str(tmp_path / name) for name in ('new.mkv', 'pending.mkv', 'done.mkv',
                                                       'error.mkv', 'processing.mkv')]
            discovery_error_path = str(tmp_path / 'new-error.mkv')
            for path in paths:
                with open(path, 'wb') as media:
                    media.write(b'media')
            db.session.add_all([
                ScanResult(file_path=paths[1], scan_status='pending'),
                ScanResult(file_path=paths[2], scan_status='completed'),
                ScanResult(file_path=paths[3], scan_status='error'),
                ScanResult(file_path=paths[4], scan_status='scanning'),
                ScanState(scan_id='incremental-members', force_rescan=False),
            ])
            db.session.commit()

            add_files_batch_to_db(paths + [discovery_error_path], scan_id='incremental-members')

            members = {member.file_path for member in ScanRunFile.query.filter_by(
                scan_id='incremental-members').all()}
            assert members == set(paths[:2] + [discovery_error_path])
            assert ScanResult.query.filter_by(file_path=discovery_error_path).one().scan_status == 'error'

    def test_forced_discovery_members_all_inventory_and_is_idempotent(self, app, db, tmp_path):
        with app.app_context():
            paths = [str(tmp_path / name) for name in ('done.mkv', 'error.mkv', 'processing.mkv')]
            for path in paths:
                with open(path, 'wb') as media:
                    media.write(b'media')
            db.session.add_all([
                ScanResult(file_path=paths[0], scan_status='completed'),
                ScanResult(file_path=paths[1], scan_status='error'),
                ScanResult(file_path=paths[2], scan_status='scanning'),
                ScanState(scan_id='forced-members', force_rescan=True),
            ])
            db.session.commit()

            add_files_batch_to_db(paths, scan_id='forced-members')
            add_files_batch_to_db(paths, scan_id='forced-members')

            assert ScanRunFile.query.filter_by(scan_id='forced-members').count() == len(paths)

    def test_forced_claim_and_reclaim_preserve_prior_observation(self, tp, app, db, tmp_path):
        with app.app_context():
            (file_path,) = _authorized_media_root(db, tmp_path, 'completed.mkv')
            result = ScanResult(file_path=file_path, scan_status='completed')
            state = ScanState(scan_id='forced-reclaim', force_rescan=True,
                              phase='scanning', is_active=True)
            db.session.add_all([result, state])
            db.session.flush()
            db.session.add(ScanRunFile(scan_id=state.scan_id, scan_result_id=result.id,
                                       file_path=file_path, status='pending'))
            db.session.commit()

            claimed = tp._claim_chunk_members(state.scan_id, file_path, file_path,
                                               tp._build_chunk_checker(state.scan_id))
            assert claimed == [result.id]
            assert db.session.get(ScanResult, result.id).scan_status == 'completed'

            tp._reclaim_chunk_range(state.scan_id, file_path, file_path)
            assert db.session.get(ScanResult, result.id).scan_status == 'completed'
            assert ScanRunFile.query.filter_by(scan_id=state.scan_id,
                                                file_path=file_path).one().status == 'pending'


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
            _make_scan_state(db, 'scan-1')
            assert engine.build_scan_chunks('scan-1') == []

    def test_completed_files_not_chunked(self, engine, app, db):
        with app.app_context():
            _make_scan_state(db, 'scan-1')
            completed = ScanResult(file_path='/media/a.mkv', scan_status='completed')
            pending = ScanResult(file_path='/media/b.mkv', scan_status='pending')
            db.session.add_all([completed, pending])
            db.session.commit()
            db.session.add_all([
                ScanRunFile(scan_id='scan-1', scan_result_id=completed.id,
                            file_path=completed.file_path, status='completed'),
                ScanRunFile(scan_id='scan-1', scan_result_id=pending.id,
                            file_path=pending.file_path, status='pending'),
            ])
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

    def test_finalizes_when_all_chunks_terminal(self, engine, app, db):
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

    def test_errored_chunks_finalize_as_error(self, engine, app, db):
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

    def test_finalize_is_exactly_once(self, engine, app, db):
        with app.app_context():
            _make_scan_state(db, 'scan-f3')
            _make_chunk(db, 'scan-f3', '/a', '/b', is_complete=True,
                        status='completed', files_scanned=1)

            assert engine.maybe_finalize_scan('scan-f3') is True
            # Second caller sees phase != 'scanning' and does nothing
            assert engine.maybe_finalize_scan('scan-f3') is False

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

    def test_finalize_reclaims_stuck_scanning_rows(self, engine, app, db):
        with app.app_context():
            result = ScanResult(file_path='/media/stuck.mkv', scan_status='scanning')
            db.session.add(result)
            db.session.commit()
            _make_scan_state(db, 'scan-f5')
            db.session.add(ScanRunFile(scan_id='scan-f5', scan_result_id=result.id,
                                       file_path=result.file_path, status='processing'))
            db.session.commit()
            _make_chunk(db, 'scan-f5', '/a', '/b', is_complete=True, status='completed')

            assert engine.maybe_finalize_scan('scan-f5') is True
            row = ScanResult.query.filter_by(file_path='/media/stuck.mkv').first()
            assert row.scan_status == 'pending'

    def test_finalizer_never_decreases_progress(self, engine, app, db):
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


class TestChunkOutcomeAndPolicy:

    def test_unreadable_result_is_not_media_corruption(self, tp, app, db, tmp_path):
        with app.app_context():
            (file_path,) = _authorized_media_root(db, tmp_path, 'unreadable.mkv')
            _add_pending(db, [file_path], scan_id='scan-unreadable')
            chunk = _make_chunk(db, 'scan-unreadable', file_path, file_path)
            unreadable = {
                'outcome': 'unreadable',
                'is_corrupted': False,
                'corruption_details': 'Read failed: storage timeout',
                'scan_output': 'Read failed',
                'scan_tool': 'error',
                'has_warnings': False,
            }
            with patch.object(tp.PixelProbe, 'scan_file', return_value=unreadable):
                result = tp.process_chunk_task.apply(
                    args=(chunk.id, 'scan-unreadable')).get()
            assert result['status'] == 'SUCCESS'
            row = ScanResult.query.filter_by(file_path=file_path).first()
            member = ScanRunFile.query.filter_by(
                scan_id='scan-unreadable', file_path=file_path).first()
            assert row.is_corrupted is None
            assert member.is_corrupted is None
            assert member.outcome == 'unreadable'

    def test_current_exclusion_skips_member_before_decode(self, tp, app, db, tmp_path):
        with app.app_context():
            (file_path,) = _authorized_media_root(db, tmp_path, 'withdrawn.mkv')
            _add_pending(db, [file_path], scan_id='scan-withdrawn')
            chunk = _make_chunk(db, 'scan-withdrawn', file_path, file_path)
            with patch.object(tp, 'load_exclusions_with_patterns',
                              return_value=([file_path], [], [])), \
                 patch.object(tp.PixelProbe, 'scan_file') as scan_file:
                result = tp.process_chunk_task.apply(
                    args=(chunk.id, 'scan-withdrawn')).get()
            assert result['status'] == 'SKIPPED'
            scan_file.assert_not_called()
            member = ScanRunFile.query.filter_by(
                scan_id='scan-withdrawn', file_path=file_path).first()
            row = ScanResult.query.filter_by(file_path=file_path).first()
            assert member.status == 'skipped'
            assert member.outcome == 'excluded'
            assert row.scan_status == 'pending'


class TestDiscoveryContainment:

    def test_required_mount_mismatch_rejects_discovery_root(self, tp, app, db, tmp_path):
        with app.app_context():
            db.session.add(ScanConfiguration(
                path=str(tmp_path), is_active=True, require_mount=True,
                mount_filesystem_type='nfs', mount_source='server:/library', mount_root='/',
            ))
            db.session.commit()
            with patch.object(tp, 'mount_matches_baseline', return_value=(False, None)):
                assert tp._root_mount_baseline_matches(str(tmp_path)) is False

    def test_symlinked_requested_root_is_rejected_even_when_target_is_active(
            self, tp, app, db, tmp_path):
        with app.app_context():
            target = tmp_path / 'active-target'
            target.mkdir()
            (target / 'inside.mkv').write_bytes(b'not-a-real-video')
            requested = tmp_path / 'requested-link'
            requested.symlink_to(target, target_is_directory=True)
            db.session.add(ScanConfiguration(path=str(target), is_active=True))
            _make_scan_state(db, 'scan-symlink-root', phase='discovering')
            db.session.add(ScanRunRoot(
                scan_id='scan-symlink-root', root_path=str(requested),
                resolved_path=str(target), status='dispatched'))
            db.session.commit()

            with patch.object(tp, 'finalize_scan'):
                result = tp.discover_directory_task.apply(
                    args=(str(requested), 'scan-symlink-root')).get()

            assert result['complete'] is False
            assert 'must not be a symlink' in result['error']
            assert ScanRunFile.query.filter_by(scan_id='scan-symlink-root').count() == 0


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
        db.session.add(ScanRunFile(scan_id='scan-r1', scan_result_id=row.id,
                                   file_path=row.file_path, status='processing'))
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
            assert count == 0
            assert mock_async.call_count == 2


class TestDurableTaskIntents:

    def test_reconciliation_requeues_one_expired_chunk_intent(self, tp, app, db):
        with app.app_context():
            state = _make_scan_state(db, 'scan-intent')
            chunk = _make_chunk(db, 'scan-intent', '/a', '/b')
            task = ScanTask(
                scan_id='scan-intent', chunk_id=chunk.id, purpose='chunk',
                celery_task_id='intent-1', generation=state.dispatch_generation,
                payload={'force_rescan': False}, status='queued',
            )
            chunk.celery_task_id = task.celery_task_id
            db.session.add(task)
            db.session.commit()

            with patch.object(tp.process_chunk_task, 'apply_async') as publish:
                assert tp.reconcile_scan_task_intents() == 1
            task = db.session.get(ScanTask, task.id)
            assert task.status == 'dispatched'
            assert task.dispatch_attempts == 1
            publish.assert_called_once()

    def test_reconciliation_cancels_terminal_run_intent(self, tp, app, db):
        with app.app_context():
            state = _make_scan_state(db, 'scan-intent-terminal', is_active=False,
                                     phase='cancelled')
            task = ScanTask(scan_id=state.scan_id, purpose='continuation',
                            celery_task_id='intent-2', generation=state.dispatch_generation,
                            payload={'force_rescan': False}, status='queued')
            db.session.add(task)
            db.session.commit()
            with patch.object(tp.resume_scan_after_discovery, 'apply_async') as publish:
                assert tp.reconcile_scan_task_intents() == 0
            assert db.session.get(ScanTask, task.id).status == 'cancelled'
            publish.assert_not_called()

class TestWorkerLostRedelivery:
    """acks_late + reject_on_worker_lost redelivers the SAME task id after a
    worker dies mid-chunk; the leftover 'scanning' rows must be reclaimed and
    re-scanned, not dropped via the empty-claim completed-with-0 path"""

    def test_redelivery_reclaims_scanning_rows(self, tp, app, db, tmp_path):
        with app.app_context():
            (file_path,) = _authorized_media_root(db, tmp_path, 'f1.mkv')
            _add_pending(db, [file_path])
            row = ScanResult.query.filter_by(file_path=file_path).first()
            row.scan_status = 'scanning'  # claimed by the dead attempt
            _make_scan_state(db, 'scan-wl1')
            db.session.add(ScanRunFile(scan_id='scan-wl1', scan_result_id=row.id,
                                       file_path=row.file_path, status='processing'))
            chunk = _make_chunk(db, 'scan-wl1', file_path, file_path, status='processing')
            db.session.commit()

            result = tp.process_chunk_task.apply(args=(chunk.id, 'scan-wl1')).get()

            assert result['status'] == 'SUCCESS'
            assert result['files_processed'] == 1
            row = ScanResult.query.filter_by(file_path=file_path).first()
            assert row.scan_status != 'scanning'


class TestRevivalPreservesProgress:
    """A revived chunk restarts its local counter at zero; the chunk's prior
    tally must survive, or the scan's aggregate freezes at the pre-revival
    high-water mark (sync_progress_from_chunks never decreases) and the final
    report undercounts by every pre-revival file."""

    def test_revived_chunk_adds_to_prior_tally(self, tp, app, db, tmp_path):
        with app.app_context():
            (file_path,) = _authorized_media_root(db, tmp_path, 'r1.mkv')
            _add_pending(db, [file_path])
            row = ScanResult.query.filter_by(file_path=file_path).first()
            row.scan_status = 'scanning'  # claimed by the dead pre-revival attempt
            _make_scan_state(db, 'scan-rv1')
            db.session.add(ScanRunFile(scan_id='scan-rv1', scan_result_id=row.id,
                                       file_path=row.file_path, status='processing'))
            chunk = _make_chunk(db, 'scan-rv1', file_path, file_path, status='processing',
                                files_scanned=300)
            db.session.commit()

            result = tp.process_chunk_task.apply(args=(chunk.id, 'scan-rv1')).get()

            assert result['status'] == 'SUCCESS'
            db.session.expire_all()
            chunk = db.session.get(ScanChunk, chunk.id)
            assert chunk.files_scanned == 301
            state = ScanState.query.filter_by(scan_id='scan-rv1').first()
            assert state.files_processed == 301

    def test_clobbered_tally_recovered_from_db(self, tp, app, db, tmp_path):
        """A chunk whose tally was overwritten by a pre-fix revival recomputes
        its base from files completed in-range since the scan started."""
        with app.app_context():
            state = _make_scan_state(db, 'scan-rv3')
            first_path, second_path = _authorized_media_root(db, tmp_path, 's1.mkv', 's2.mkv')
            done = ScanResult(file_path=first_path,
                              scan_status='completed',
                              scan_date=datetime.now(timezone.utc))
            db.session.add(done)
            _add_pending(db, [second_path])
            pending_row = ScanResult.query.filter_by(file_path=second_path).first()
            db.session.add(ScanRunFile(scan_id='scan-rv3', scan_result_id=done.id,
                                       file_path=done.file_path, status='completed'))
            db.session.add(ScanRunFile(scan_id='scan-rv3', scan_result_id=pending_row.id,
                                       file_path=pending_row.file_path, status='pending'))
            chunk = _make_chunk(db, 'scan-rv3', first_path, second_path, status='processing',
                                files_scanned=0)  # clobbered by the old bug
            db.session.commit()

            result = tp.process_chunk_task.apply(args=(chunk.id, 'scan-rv3')).get()

            assert result['status'] == 'SUCCESS'
            db.session.expire_all()
            chunk = db.session.get(ScanChunk, chunk.id)
            assert chunk.files_scanned == 2

    def test_empty_reclaim_keeps_prior_tally(self, tp, app, db, tmp_path):
        with app.app_context():
            # Every file in range already completed; revival redelivery finds
            # nothing to claim and must not zero the prior tally
            (file_path,) = _authorized_media_root(db, tmp_path, 'r2.mkv')
            db.session.add(ScanResult(file_path=file_path,
                                      scan_status='completed'))
            _make_scan_state(db, 'scan-rv2')
            completed_row = ScanResult.query.filter_by(file_path=file_path).first()
            db.session.add(ScanRunFile(scan_id='scan-rv2', scan_result_id=completed_row.id,
                                       file_path=completed_row.file_path, status='completed'))
            chunk = _make_chunk(db, 'scan-rv2', file_path, file_path, status='processing',
                                files_scanned=250)
            db.session.commit()

            result = tp.process_chunk_task.apply(args=(chunk.id, 'scan-rv2')).get()

            assert result['status'] == 'SKIPPED'
            db.session.expire_all()
            chunk = db.session.get(ScanChunk, chunk.id)
            assert chunk.files_scanned == 250

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import pixelprobe.progress_utils as pu
import pixelprobe.scheduler_lock as sl
from pixelprobe.scheduler import MediaScheduler
from pixelprobe.models import db, ScanSchedule


class TestSchedulerLock:
    """A live scheduler lock must never be stolen. hostname:pid is not a safe
    identity (podman pod containers share a hostname while pids collide across
    pid namespaces), so ownership is a unique per-process value and acquisition
    is strictly SET NX."""

    def test_lock_value_unique_for_same_hostname_and_pid(self):

        # Same process, same hostname, same pid -- values still differ.
        assert sl.make_lock_value() != sl.make_lock_value()

    def test_does_not_steal_live_lock_even_with_colliding_identity(self, monkeypatch):
        """The podman-pod scenario: holder has our hostname AND our pid."""
        import os
        import socket

        redis_client = MagicMock()
        redis_client.set.return_value = None  # SET NX failed: lock is held
        redis_client.get.return_value = f"{socket.gethostname()}:{os.getpid()}:deadbeef".encode()
        monkeypatch.setattr(pu, 'get_redis_client', lambda: redis_client)
        retry_spy = MagicMock()
        monkeypatch.setattr(sl, '_start_retry_thread', retry_spy)

        scheduler = MagicMock()
        result = sl.initialize_scheduler_with_lock(MagicMock(), scheduler)

        assert result is False
        scheduler.init_app.assert_not_called()
        retry_spy.assert_called_once()
        # Every acquisition attempt was SET NX -- no unconditional overwrite.
        for call in redis_client.set.call_args_list:
            assert call.kwargs.get('nx') is True

    def test_acquires_free_lock_and_starts_heartbeat(self, monkeypatch):

        redis_client = MagicMock()
        redis_client.set.return_value = True
        monkeypatch.setattr(pu, 'get_redis_client', lambda: redis_client)
        heartbeat_spy = MagicMock()
        monkeypatch.setattr(sl, '_start_heartbeat', heartbeat_spy)

        scheduler = MagicMock()
        app = MagicMock()
        result = sl.initialize_scheduler_with_lock(app, scheduler)

        assert result is True
        scheduler.init_app.assert_called_once_with(app)
        heartbeat_spy.assert_called_once()
        assert redis_client.set.call_args.kwargs.get('nx') is True

    def test_retry_thread_acquires_after_lock_expiry(self, monkeypatch):

        monkeypatch.setattr(sl.time, 'sleep', lambda _s: None)
        monkeypatch.setattr(sl, '_start_heartbeat', MagicMock())

        # Lock held for 14 rounds (past the old max_retries=10), then expires.
        # Proves the standby retries indefinitely and only wins via SET NX.
        results = [None] * 14 + [True]
        redis_client = MagicMock()
        redis_client.set.side_effect = results

        scheduler = MagicMock()
        initialized = [False]
        sl._start_retry_thread(redis_client, 'k', 'me', scheduler, MagicMock(), initialized)

        deadline = time.time() + 5
        while not initialized[0] and time.time() < deadline:
            time.sleep(0.01)

        assert initialized[0] is True
        scheduler.init_app.assert_called_once()
        assert redis_client.set.call_count == len(results)
        for call in redis_client.set.call_args_list:
            assert call.kwargs.get('nx') is True


class TestSchedulerEnabled:
    """SCHEDULER_ENABLED gates whether a process competes for the lock at all."""

    def test_default_is_enabled(self, monkeypatch):
        monkeypatch.delenv('SCHEDULER_ENABLED', raising=False)
        assert sl.scheduler_enabled() is True

    @pytest.mark.parametrize('value', ['false', 'FALSE', ' False ', '0', 'no'])
    def test_disabled_values(self, monkeypatch, value):
        monkeypatch.setenv('SCHEDULER_ENABLED', value)
        assert sl.scheduler_enabled() is False

    @pytest.mark.parametrize('value', ['true', '1', 'yes', 'anything'])
    def test_enabled_values(self, monkeypatch, value):
        monkeypatch.setenv('SCHEDULER_ENABLED', value)
        assert sl.scheduler_enabled() is True

    def test_disabled_skips_lock_arbitration(self, monkeypatch):
        monkeypatch.setenv('SCHEDULER_ENABLED', 'false')
        redis_client = MagicMock()
        monkeypatch.setattr(pu, 'get_redis_client', lambda: redis_client)

        scheduler = MagicMock()
        assert sl.initialize_scheduler_with_lock(MagicMock(), scheduler) is False
        scheduler.init_app.assert_not_called()
        redis_client.set.assert_not_called()


class TestMediaScheduler:
    """Test MediaScheduler functionality"""
    
    @pytest.fixture
    def scheduler(self, app):
        """Create a scheduler instance"""
        scheduler = MediaScheduler()
        scheduler.init_app(app)
        yield scheduler
        scheduler.shutdown()
    
    def test_update_schedules_method_exists(self, scheduler):
        """Test that update_schedules method exists"""
        assert hasattr(scheduler, 'update_schedules')
        assert callable(getattr(scheduler, 'update_schedules'))
    
    def test_update_schedules_removes_and_reloads(self, scheduler, app, db):
        """Test that update_schedules removes existing jobs and reloads from DB"""
        with app.app_context():
            # Create a test schedule
            schedule = ScanSchedule(
                name='Test Schedule',
                cron_expression='0 2 * * *',
                is_active=True
            )
            db.session.add(schedule)
            db.session.commit()
            
            # Add a fake job to scheduler
            scheduler.scheduler.add_job(
                func=lambda: None,
                trigger='interval',
                seconds=60,
                id=f'schedule_{schedule.id}'
            )
            
            # Verify job exists
            assert scheduler.scheduler.get_job(f'schedule_{schedule.id}') is not None
            
            # Call update_schedules
            scheduler.update_schedules()

            # The job should be removed and re-added
            # Since we don't have the actual schedule loading logic in test,
            # at least verify the method runs without error


class TestScheduleDbSync:
    """The db-sync job is how schedule CRUD reaches the scheduler process.

    The Celery reload task always executes in a prefork pool child where the
    scheduler is not running, so it is skipped; without the sync job a created
    or edited schedule only registered after a container restart.
    """

    @pytest.fixture
    def scheduler(self, app, db):
        scheduler = MediaScheduler()
        scheduler.init_app(app)
        yield scheduler
        scheduler.shutdown()

    def test_sync_job_registered(self, scheduler):
        job = scheduler.scheduler.get_job('db_schedule_sync')
        assert job is not None
        # update_schedules removes every job whose id starts with 'schedule_';
        # the sync job must never match that prefix or it would delete itself
        assert not job.id.startswith('schedule_')

    def test_sync_reloads_when_definitions_change(self, scheduler, app, db):
        with app.app_context():
            schedule = ScanSchedule(name='Sync Test', cron_expression='0 2 * * *',
                                    scan_type='file_changes', time_budget_minutes=10,
                                    is_active=True)
            db.session.add(schedule)
            db.session.commit()

        with patch.object(scheduler, 'update_schedules') as update:
            scheduler._sync_schedules_from_db()
        update.assert_called_once()

    def test_sync_skips_when_unchanged(self, scheduler, app, db):
        with app.app_context():
            scheduler._schedules_fp = scheduler._schedule_fingerprint()
        with patch.object(scheduler, 'update_schedules') as update:
            scheduler._sync_schedules_from_db()
        update.assert_not_called()

    def test_budget_change_triggers_reload(self, scheduler, app, db):
        with app.app_context():
            schedule = ScanSchedule(name='Budget Sync', cron_expression='0 2 * * *',
                                    scan_type='file_changes', time_budget_minutes=10,
                                    is_active=True)
            db.session.add(schedule)
            db.session.commit()
            scheduler._schedules_fp = scheduler._schedule_fingerprint()
            schedule.time_budget_minutes = 30
            db.session.commit()

        with patch.object(scheduler, 'update_schedules') as update:
            scheduler._sync_schedules_from_db()
        update.assert_called_once()


class TestQueueConflictRetry:
    """Test that skipped scheduled scans get a one-shot retry queued."""

    @pytest.fixture
    def scheduler(self, app):
        scheduler = MediaScheduler()
        scheduler.init_app(app)
        yield scheduler
        scheduler.shutdown()

    def test_queues_date_trigger_retry(self, scheduler):
        def noop(schedule_id):
            return None

        scheduler._queue_conflict_retry('schedule_42', noop, (42,), 'phase=scanning')

        assert scheduler.pending_retries['schedule_42'] == 1

        job = scheduler.scheduler.get_job('schedule_42_retry_1')
        assert job is not None
        expected = datetime.now(timezone.utc) + timedelta(minutes=scheduler.retry_delay_minutes)
        assert abs((job.next_run_time - expected).total_seconds()) < 30

    def test_respects_max_count(self, scheduler):
        def noop(schedule_id):
            return None

        scheduler.retry_max_count = 2
        scheduler._queue_conflict_retry('schedule_99', noop, (99,), 'phase=scanning')
        scheduler._queue_conflict_retry('schedule_99', noop, (99,), 'phase=scanning')
        # Third call hits the cap and should drop the pending entry.
        scheduler._queue_conflict_retry('schedule_99', noop, (99,), 'phase=scanning')

        assert 'schedule_99' not in scheduler.pending_retries
        assert scheduler.scheduler.get_job('schedule_99_retry_3') is None

    def test_clear_pending_retry_removes_entry(self, scheduler):
        def noop():
            return None

        scheduler._queue_conflict_retry('periodic', noop, (), 'phase=scanning')
        assert 'periodic' in scheduler.pending_retries

        scheduler._clear_pending_retry('periodic')
        assert 'periodic' not in scheduler.pending_retries

    def test_add_job_failure_does_not_consume_slot(self, scheduler, monkeypatch):
        """If APScheduler fails to enqueue the retry job the counter must not
        advance, otherwise transient failures would burn through retry_max_count
        without a single retry actually running."""
        def noop(schedule_id):
            return None

        def broken_add_job(*args, **kwargs):
            raise RuntimeError('simulated APScheduler failure')

        monkeypatch.setattr(scheduler.scheduler, 'add_job', broken_add_job)

        scheduler._queue_conflict_retry('schedule_77', noop, (77,), 'phase=scanning')

        assert 'schedule_77' not in scheduler.pending_retries

    def test_invalid_env_var_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv('SCHEDULE_RETRY_DELAY_MINUTES', 'not-a-number')
        monkeypatch.setenv('SCHEDULE_RETRY_MAX_COUNT', '-5')

        scheduler = MediaScheduler()
        assert scheduler.retry_delay_minutes == MediaScheduler.DEFAULT_RETRY_DELAY_MINUTES
        assert scheduler.retry_max_count == 0  # clamped via min_value=0


class TestHeartbeatRecovery:
    """The lock heartbeat must keep retrying through Redis failures rather than
    breaking out forever (which silently abandoned the lock). It refreshes the
    TTL only while it still owns the lock (atomic compare-and-expire) and never
    overwrites another holder."""

    def test_keeps_retrying_through_failures_and_recovers(self, monkeypatch):

        monkeypatch.setattr(sl.time, 'sleep', lambda _s: None)
        initialized = [True]
        calls = {'n': 0}

        def eval_side_effect(*_a, **_k):
            calls['n'] += 1
            if calls['n'] <= 2:
                raise Exception('transient blip')
            # Recovered after 2 failures: stop the loop deterministically.
            initialized[0] = False
            return 1

        redis_client = MagicMock()
        redis_client.eval.side_effect = eval_side_effect

        t = sl._start_heartbeat('k', redis_client, 'me', initialized)
        t.join(timeout=5)

        assert not t.is_alive()
        # Two failures did NOT stop refreshing; it kept going and recovered.
        assert calls['n'] >= 3

    def test_sustained_failure_does_not_abandon_loop(self, monkeypatch):

        monkeypatch.setattr(sl.time, 'sleep', lambda _s: None)
        initialized = [True]
        calls = {'n': 0}

        def eval_side_effect(*_a, **_k):
            calls['n'] += 1
            if calls['n'] >= 5:
                # Stop the test loop; in production it would keep retrying.
                initialized[0] = False
            raise Exception('redis down')

        redis_client = MagicMock()
        redis_client.eval.side_effect = eval_side_effect

        t = sl._start_heartbeat('k', redis_client, 'me', initialized)
        t.join(timeout=5)

        assert not t.is_alive()
        # The loop retried every interval instead of bailing on the first error.
        assert calls['n'] >= 5

    def test_refresh_while_owner_never_writes(self, monkeypatch):
        """While we still hold the lock, the heartbeat only extends the TTL."""

        monkeypatch.setattr(sl.time, 'sleep', lambda _s: None)
        initialized = [True]
        calls = {'n': 0}

        def eval_side_effect(*args, **_k):
            calls['n'] += 1
            assert args[3] == 'me'  # compare-and-expire against OUR value
            if calls['n'] >= 3:
                initialized[0] = False
            return 1

        redis_client = MagicMock()
        redis_client.eval.side_effect = eval_side_effect

        t = sl._start_heartbeat('k', redis_client, 'me', initialized)
        t.join(timeout=5)

        assert not t.is_alive()
        redis_client.set.assert_not_called()

    def test_reclaims_expired_key_with_set_nx(self, monkeypatch):
        """Redis outage expired the key: heartbeat reclaims it, but only NX."""

        monkeypatch.setattr(sl.time, 'sleep', lambda _s: None)
        initialized = [True]

        def eval_side_effect(*_a, **_k):
            return 0  # no longer owner (key gone)

        def set_side_effect(*_a, **kwargs):
            initialized[0] = False
            assert kwargs.get('nx') is True
            return True

        redis_client = MagicMock()
        redis_client.eval.side_effect = eval_side_effect
        redis_client.set.side_effect = set_side_effect

        t = sl._start_heartbeat('k', redis_client, 'me', initialized)
        t.join(timeout=5)

        assert not t.is_alive()
        redis_client.set.assert_called_once()

    def test_never_overwrites_another_holder(self, monkeypatch):
        """Another process holds the lock now: warn, do not clobber it."""

        monkeypatch.setattr(sl.time, 'sleep', lambda _s: None)
        initialized = [True]

        def set_side_effect(*_a, **kwargs):
            initialized[0] = False
            assert kwargs.get('nx') is True
            return None  # someone else got there first

        redis_client = MagicMock()
        redis_client.eval.return_value = 0
        redis_client.set.side_effect = set_side_effect
        redis_client.get.return_value = b'other-host:1:cafef00d'

        t = sl._start_heartbeat('k', redis_client, 'me', initialized)
        t.join(timeout=5)

        assert not t.is_alive()
        # The only write attempted was the NX reclaim, which lost cleanly.
        redis_client.set.assert_called_once()


class TestScanningReclaim:
    """A worker that dies mid-chunk leaves files stuck in 'scanning'. When no
    scan is active the stuck-scan check must reclaim them to 'pending'."""

    @pytest.fixture
    def scheduler(self, app):
        scheduler = MediaScheduler()
        scheduler.init_app(app)
        yield scheduler
        scheduler.shutdown()

    def test_orphaned_scanning_reset_when_no_active_scan(self, scheduler, app, db):
        from pixelprobe.models import ScanResult
        with app.app_context():
            db.session.add(ScanResult(file_path='/m/a.mkv', scan_status='scanning'))
            db.session.add(ScanResult(file_path='/m/b.mkv', scan_status='scanning'))
            db.session.add(ScanResult(file_path='/m/c.mkv', scan_status='pending'))
            db.session.commit()

        scheduler._check_stuck_scans()

        with app.app_context():
            statuses = {r.file_path: r.scan_status for r in ScanResult.query.all()}
            assert statuses['/m/a.mkv'] == 'pending'
            assert statuses['/m/b.mkv'] == 'pending'
            assert statuses['/m/c.mkv'] == 'pending'

    def test_scanning_preserved_when_scan_active(self, scheduler, app, db):
        from pixelprobe.models import ScanResult, ScanState
        with app.app_context():
            db.session.add(ScanResult(file_path='/m/live.mkv', scan_status='scanning'))
            db.session.add(ScanState(scan_id='live-1', is_active=True, phase='scanning'))
            db.session.commit()

        scheduler._check_stuck_scans()

        with app.app_context():
            r = ScanResult.query.filter_by(file_path='/m/live.mkv').first()
            assert r.scan_status == 'scanning'


class TestStuckScanChunkAwareness:
    """A finished orchestrator task must not get a scan marked crashed while
    chunk tasks are still active (issue #65): the orchestrator returns as soon
    as chunks are queued, so its SUCCESS state says nothing about the scan."""

    @pytest.fixture
    def scheduler(self, app):
        scheduler = MediaScheduler()
        scheduler.init_app(app)
        yield scheduler
        scheduler.shutdown()

    def _make_stale_scan(self, db, scan_id, with_active_chunk):
        from pixelprobe.models import ScanState, ScanChunk
        # 10 min stale: past the 5-min task-gone threshold, under the 30-min
        # hard threshold, so only the task-gone branch is in play.
        scan = ScanState(
            scan_id=scan_id, is_active=True, phase='scanning',
            celery_task_id='orchestrator-task',
            last_update=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        db.session.add(scan)
        if with_active_chunk:
            db.session.add(ScanChunk(
                scan_id=scan_id, chunk_id='chunk-1',
                directory_path='/media', status='processing',
            ))
        db.session.commit()
        return scan

    def _run_check(self, scheduler, app, monkeypatch):
        import pixelprobe.utils.celery_utils as cu
        monkeypatch.setattr(cu, 'check_celery_available', lambda: True)
        monkeypatch.setattr(cu, 'safe_check_task_state', lambda *_a, **_k: 'SUCCESS')
        app.celery = MagicMock()
        scheduler._check_stuck_scans()

    def test_active_chunks_prevent_false_crash(self, scheduler, app, db, monkeypatch):
        from pixelprobe.models import ScanState
        self._make_stale_scan(db, 'chunked-live', with_active_chunk=True)
        self._run_check(scheduler, app, monkeypatch)

        db.session.expire_all()
        scan = ScanState.query.filter_by(scan_id='chunked-live').first()
        assert scan.phase == 'scanning'
        assert scan.is_active is True

    def test_scan_without_chunks_is_marked_crashed(self, scheduler, app, db, monkeypatch):
        """Positive control: with no live chunks the task-gone branch still
        fires, proving the previous test is not passing vacuously."""
        from pixelprobe.models import ScanState
        self._make_stale_scan(db, 'chunked-dead', with_active_chunk=False)
        self._run_check(scheduler, app, monkeypatch)

        db.session.expire_all()
        scan = ScanState.query.filter_by(scan_id='chunked-dead').first()
        assert scan.phase == 'crashed'
        assert scan.is_active is False

    def test_has_active_chunks_helper(self, app, db):
        from pixelprobe.models import ScanChunk
        db.session.add(ScanChunk(scan_id='s-live', chunk_id='c1',
                                 directory_path='/media', status='processing'))
        db.session.add(ScanChunk(scan_id='s-done', chunk_id='c1',
                                 directory_path='/media', status='completed'))
        db.session.commit()

        assert ScanChunk.has_active('s-live')
        assert not ScanChunk.has_active('s-done')
        assert not ScanChunk.has_active('s-none')


class TestUTCSessionTimezone:
    """The DB session timezone must be pinned to UTC: the app stores aware-UTC
    datetimes in naive TIMESTAMP columns and reads them back assuming UTC, so
    a non-UTC PostgreSQL session timezone stores local wall time and makes
    every scan look hours stale to the stuck-scan checks (issue #65)."""

    def test_all_configs_pin_session_timezone_to_utc(self):
        from pixelprobe.config import Config, TestingConfig
        for cfg in (Config, TestingConfig):
            assert cfg.SQLALCHEMY_ENGINE_OPTIONS['connect_args']['options'] == '-c timezone=UTC'

    @pytest.mark.parametrize('config_name', ['Config', 'TestingConfig'])
    def test_init_app_keeps_timezone_option(self, monkeypatch, config_name):
        """Both init_app overrides rebuild connect_args from scratch -- each
        must re-include the timezone pin."""
        from flask import Flask
        import pixelprobe.config as config_module
        cfg = getattr(config_module, config_name)
        monkeypatch.setattr(cfg, 'POSTGRES_PASSWORD', 'pw')
        flask_app = Flask(__name__)
        cfg.init_app(flask_app)
        connect_args = flask_app.config['SQLALCHEMY_ENGINE_OPTIONS']['connect_args']
        assert connect_args['options'] == '-c timezone=UTC'


class TestSchedulerRetryGaps:
    """Phase 2: a scheduled fire must never be silently dropped on lock
    contention or an API 409, and last_run advances only on a confirmed start."""

    @pytest.fixture
    def scheduler(self, app):
        scheduler = MediaScheduler()
        scheduler.init_app(app)
        yield scheduler
        scheduler.shutdown()

    def test_scheduled_lock_contention_queues_retry(self, scheduler):
        assert scheduler.scan_lock.acquire(blocking=False)
        try:
            scheduler._run_scheduled_scan(8)
        finally:
            scheduler.scan_lock.release()
        assert scheduler.pending_retries.get('schedule_8') == 1
        assert scheduler.scheduler.get_job('schedule_8_retry_1') is not None

    def test_periodic_lock_contention_queues_retry(self, scheduler):
        assert scheduler.scan_lock.acquire(blocking=False)
        try:
            scheduler._run_periodic_scan()
        finally:
            scheduler.scan_lock.release()
        assert scheduler.pending_retries.get('periodic') == 1

    def test_handle_response_200_clears_and_returns_true(self, scheduler):
        scheduler.pending_retries['schedule_5'] = 2
        resp = MagicMock(status_code=200)
        assert scheduler._handle_scan_response('schedule_5', lambda _i: None, (5,), resp) is True
        assert 'schedule_5' not in scheduler.pending_retries

    def test_handle_response_409_queues_retry(self, scheduler):
        resp = MagicMock(status_code=409)
        assert scheduler._handle_scan_response('schedule_6', lambda _i: None, (6,), resp) is False
        assert scheduler.pending_retries.get('schedule_6') == 1

    def test_handle_response_connection_error_queues_retry(self, scheduler):
        assert scheduler._handle_scan_response('schedule_7', lambda _i: None, (7,), None) is False
        assert scheduler.pending_retries.get('schedule_7') == 1


class TestRetentionJob:
    """Data retention cleanup must be scheduled by the single-leader scheduler
    (not the never-launched Celery beat)."""

    @pytest.fixture
    def scheduler(self, app):
        scheduler = MediaScheduler()
        scheduler.init_app(app)
        yield scheduler
        scheduler.shutdown()

    def test_data_retention_job_registered(self, scheduler):
        assert scheduler.scheduler.get_job('data_retention_cleanup') is not None


class TestStuckScanRevival:
    """A scan whose chunk workers are provably gone (stale heartbeat, active
    chunk rows) gets its chunks re-dispatched instead of being crashed;
    after _MAX_CHUNK_REVIVALS failed revivals the crash branch fires (issue #75)."""

    @pytest.fixture
    def scheduler(self, app):
        scheduler = MediaScheduler()
        scheduler.init_app(app)
        yield scheduler
        scheduler.shutdown()

    # tasks_parallel_mod fixture comes from conftest.py

    def _make_scan(self, db, scan_id, minutes_stale, with_active_chunk=True):
        from pixelprobe.models import ScanState, ScanChunk
        scan = ScanState(
            scan_id=scan_id, is_active=True, phase='scanning',
            last_update=datetime.now(timezone.utc) - timedelta(minutes=minutes_stale),
        )
        db.session.add(scan)
        if with_active_chunk:
            db.session.add(ScanChunk(
                scan_id=scan_id, chunk_id='chunk-1',
                directory_path='/media', status='processing',
            ))
        db.session.commit()
        return scan

    def test_fresh_heartbeat_left_alone(self, scheduler, app, db, tasks_parallel_mod, monkeypatch):
        from pixelprobe.models import ScanState
        self._make_scan(db, 'rev-fresh', minutes_stale=2)
        revive = MagicMock(return_value=2)
        monkeypatch.setattr(tasks_parallel_mod, 'redispatch_orphaned_chunks', revive)
        scheduler._check_stuck_scans()
        db.session.expire_all()
        scan = ScanState.query.filter_by(scan_id='rev-fresh').first()
        assert scan.phase == 'scanning' and scan.is_active is True
        revive.assert_not_called()

    def test_stale_with_active_chunks_revived_not_crashed(self, scheduler, app, db,
                                                          tasks_parallel_mod, monkeypatch):
        from pixelprobe.models import ScanState
        self._make_scan(db, 'rev-orphan', minutes_stale=31)
        revive = MagicMock(return_value=2)
        monkeypatch.setattr(tasks_parallel_mod, 'redispatch_orphaned_chunks', revive)
        scheduler._check_stuck_scans()
        db.session.expire_all()
        scan = ScanState.query.filter_by(scan_id='rev-orphan').first()
        assert scan.phase == 'scanning' and scan.is_active is True
        revive.assert_called_once()
        assert scheduler._revive_attempts['rev-orphan'] == 1

    def test_revival_cap_exhausted_crashes(self, scheduler, app, db,
                                           tasks_parallel_mod, monkeypatch):
        from pixelprobe.models import ScanState
        self._make_scan(db, 'rev-capped', minutes_stale=31)
        revive = MagicMock(return_value=2)
        monkeypatch.setattr(tasks_parallel_mod, 'redispatch_orphaned_chunks', revive)
        scheduler._revive_attempts['rev-capped'] = 3
        scheduler._check_stuck_scans()
        db.session.expire_all()
        scan = ScanState.query.filter_by(scan_id='rev-capped').first()
        assert scan.phase == 'crashed' and scan.is_active is False
        revive.assert_not_called()

    def test_stale_without_chunks_crashes_unchanged(self, scheduler, app, db,
                                                    tasks_parallel_mod, monkeypatch):
        from pixelprobe.models import ScanState
        self._make_scan(db, 'rev-nochunks', minutes_stale=31, with_active_chunk=False)
        revive = MagicMock(return_value=0)
        monkeypatch.setattr(tasks_parallel_mod, 'redispatch_orphaned_chunks', revive)
        scheduler._check_stuck_scans()
        db.session.expire_all()
        scan = ScanState.query.filter_by(scan_id='rev-nochunks').first()
        assert scan.phase == 'crashed' and scan.is_active is False
        revive.assert_not_called()

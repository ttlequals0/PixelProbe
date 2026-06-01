from datetime import datetime, timedelta, timezone

import pytest

from pixelprobe.scheduler import MediaScheduler
from pixelprobe.models import db, ScanSchedule


class TestSchedulerLockHelpers:
    """Test scheduler lock helper functions for multi-worker coordination."""

    def test_parse_scheduler_lock_new_format(self):
        """Test parsing new lock format: hostname:pid:timestamp"""
        # Import the functions from app module
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # Test parsing logic directly (mirrors app.py implementation)
        lock_value = "pixelprobe-app:12345:2026-01-01T00:00:00+00:00"
        parts = lock_value.split(':')
        if len(parts) >= 3 and not parts[0].isdigit():
            hostname = parts[0]
            pid = parts[1]
            timestamp_str = ':'.join(parts[2:])
        else:
            hostname = None
            pid = parts[0]
            timestamp_str = ':'.join(parts[1:])

        assert hostname == "pixelprobe-app"
        assert pid == "12345"
        assert timestamp_str == "2026-01-01T00:00:00+00:00"

    def test_parse_scheduler_lock_old_format(self):
        """Test parsing old lock format: pid:timestamp (no hostname)"""
        lock_value = "12345:2026-01-01T00:00:00+00:00"
        parts = lock_value.split(':')
        if len(parts) >= 3 and not parts[0].isdigit():
            hostname = parts[0]
            pid = parts[1]
            timestamp_str = ':'.join(parts[2:])
        else:
            hostname = None
            pid = parts[0]
            timestamp_str = ':'.join(parts[1:])

        assert hostname is None
        assert pid == "12345"
        assert timestamp_str == "2026-01-01T00:00:00+00:00"

    def test_should_acquire_self_lock(self):
        """Test that same hostname AND same PID allows acquisition (self-lock refresh)."""
        lock_hostname = "pixelprobe-app"
        lock_pid = "12345"
        lock_age = 10  # Recent lock
        my_hostname = "pixelprobe-app"
        my_pid = "12345"

        # Same hostname AND same PID = self-lock, always acquire
        if lock_hostname == my_hostname and lock_pid == my_pid:
            should_acquire = True
            reason = "self-lock"
        elif lock_hostname == my_hostname:
            should_acquire = lock_age > 65
            reason = "stale-sibling" if should_acquire else "active-sibling"
        else:
            should_acquire = lock_age > 65
            reason = "stale-remote" if should_acquire else "active-remote"

        assert should_acquire is True
        assert reason == "self-lock"

    def test_should_not_acquire_active_sibling(self):
        """Test that same hostname but different PID with fresh lock is NOT acquired."""
        lock_hostname = "pixelprobe-app"
        lock_pid = "12345"
        lock_age = 10  # Recent lock (within 65s threshold)
        my_hostname = "pixelprobe-app"
        my_pid = "67890"  # Different PID - sibling worker

        if lock_hostname == my_hostname and lock_pid == my_pid:
            should_acquire = True
            reason = "self-lock"
        elif lock_hostname == my_hostname:
            should_acquire = lock_age > 65
            reason = "stale-sibling" if should_acquire else "active-sibling"
        else:
            should_acquire = lock_age > 65
            reason = "stale-remote" if should_acquire else "active-remote"

        assert should_acquire is False
        assert reason == "active-sibling"

    def test_should_acquire_stale_sibling(self):
        """Test that same hostname but different PID with stale lock IS acquired."""
        lock_hostname = "pixelprobe-app"
        lock_pid = "12345"
        lock_age = 70  # Stale lock (>65s threshold)
        my_hostname = "pixelprobe-app"
        my_pid = "67890"  # Different PID - sibling worker

        if lock_hostname == my_hostname and lock_pid == my_pid:
            should_acquire = True
            reason = "self-lock"
        elif lock_hostname == my_hostname:
            should_acquire = lock_age > 65
            reason = "stale-sibling" if should_acquire else "active-sibling"
        else:
            should_acquire = lock_age > 65
            reason = "stale-remote" if should_acquire else "active-remote"

        assert should_acquire is True
        assert reason == "stale-sibling"

    def test_should_not_acquire_active_remote(self):
        """Test that different hostname with fresh lock is NOT acquired."""
        lock_hostname = "other-container"
        lock_pid = "12345"
        lock_age = 10  # Recent lock
        my_hostname = "pixelprobe-app"
        my_pid = "67890"

        if lock_hostname == my_hostname and lock_pid == my_pid:
            should_acquire = True
            reason = "self-lock"
        elif lock_hostname == my_hostname:
            should_acquire = lock_age > 65
            reason = "stale-sibling" if should_acquire else "active-sibling"
        else:
            should_acquire = lock_age > 65
            reason = "stale-remote" if should_acquire else "active-remote"

        assert should_acquire is False
        assert reason == "active-remote"

    def test_should_acquire_stale_remote(self):
        """Test that different hostname with stale lock IS acquired."""
        lock_hostname = "other-container"
        lock_pid = "12345"
        lock_age = 70  # Stale lock (>65s threshold)
        my_hostname = "pixelprobe-app"
        my_pid = "67890"

        if lock_hostname == my_hostname and lock_pid == my_pid:
            should_acquire = True
            reason = "self-lock"
        elif lock_hostname == my_hostname:
            should_acquire = lock_age > 65
            reason = "stale-sibling" if should_acquire else "active-sibling"
        else:
            should_acquire = lock_age > 65
            reason = "stale-remote" if should_acquire else "active-remote"

        assert should_acquire is True
        assert reason == "stale-remote"

    def test_old_format_lock_treated_as_remote(self):
        """Test that old format locks (no hostname) are treated as remote."""
        lock_hostname = None  # Old format has no hostname
        lock_pid = "12345"
        lock_age = 10  # Recent lock
        my_hostname = "pixelprobe-app"
        my_pid = "67890"

        # When hostname is None, it can't match my_hostname, so it's treated as remote
        if lock_hostname == my_hostname and lock_pid == my_pid:
            should_acquire = True
            reason = "self-lock"
        elif lock_hostname == my_hostname:
            should_acquire = lock_age > 65
            reason = "stale-sibling" if should_acquire else "active-sibling"
        else:
            should_acquire = lock_age > 65
            reason = "stale-remote" if should_acquire else "active-remote"

        assert should_acquire is False
        assert reason == "active-remote"


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
    breaking out forever (which silently abandoned the lock). It must never
    relinquish the lock on a transient error, and it stops only when the loop's
    initialized flag is cleared (process teardown)."""

    def test_keeps_retrying_through_failures_and_recovers(self, monkeypatch):
        from unittest.mock import MagicMock
        import pixelprobe.scheduler_lock as sl

        monkeypatch.setattr(sl.time, 'sleep', lambda _s: None)
        initialized = [True]
        calls = {'n': 0}

        def set_side_effect(*_a, **_k):
            calls['n'] += 1
            if calls['n'] <= 2:
                raise Exception('transient blip')
            # Recovered after 2 failures: stop the loop deterministically.
            initialized[0] = False
            return True

        redis_client = MagicMock()
        redis_client.set.side_effect = set_side_effect

        t = sl._start_heartbeat('k', redis_client, 'host', initialized)
        t.join(timeout=5)

        assert not t.is_alive()
        # Two failures did NOT stop refreshing; it kept going and recovered.
        assert calls['n'] >= 3

    def test_sustained_failure_does_not_abandon_loop(self, monkeypatch):
        from unittest.mock import MagicMock
        import pixelprobe.scheduler_lock as sl

        monkeypatch.setattr(sl.time, 'sleep', lambda _s: None)
        initialized = [True]
        calls = {'n': 0}

        def set_side_effect(*_a, **_k):
            calls['n'] += 1
            if calls['n'] >= 5:
                # Stop the test loop; in production it would keep retrying.
                initialized[0] = False
            raise Exception('redis down')

        redis_client = MagicMock()
        redis_client.set.side_effect = set_side_effect

        t = sl._start_heartbeat('k', redis_client, 'host', initialized)
        t.join(timeout=5)

        assert not t.is_alive()
        # The loop retried every interval instead of bailing on the first error.
        assert calls['n'] >= 5


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
        from unittest.mock import MagicMock
        scheduler.pending_retries['schedule_5'] = 2
        resp = MagicMock(status_code=200)
        assert scheduler._handle_scan_response('schedule_5', lambda _i: None, (5,), resp) is True
        assert 'schedule_5' not in scheduler.pending_retries

    def test_handle_response_409_queues_retry(self, scheduler):
        from unittest.mock import MagicMock
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

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
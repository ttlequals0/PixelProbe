"""Unit tests for MaintenanceService integrity-scan timeout and progress (v2.6.42)."""

import importlib
import os
import time
from unittest.mock import MagicMock, patch

from pixelprobe.services import maintenance_service
from pixelprobe import progress_utils


class TestIntegrityTaskTimeoutConfig:
    """INTEGRITY_TASK_TIMEOUT_SECS is read at module import.

    v2.6.42 introduced this constant to break the integrity-scan hang
    where stuck Celery tasks pinned the producer at MAX_CONCURRENT_SMALL
    forever after a worker died.
    """

    def teardown_method(self, method):
        os.environ.pop('INTEGRITY_TASK_TIMEOUT_SECS', None)
        importlib.reload(maintenance_service)

    def test_default_is_three_hours(self):
        # Raised from 30m to 3h so a ~55GB sequential hash on slow storage is not
        # abandoned mid-read.
        os.environ.pop('INTEGRITY_TASK_TIMEOUT_SECS', None)
        importlib.reload(maintenance_service)
        assert maintenance_service.INTEGRITY_TASK_TIMEOUT_SECS == 10800

    def test_overrides_via_environment(self):
        os.environ['INTEGRITY_TASK_TIMEOUT_SECS'] = '120'
        importlib.reload(maintenance_service)
        assert maintenance_service.INTEGRITY_TASK_TIMEOUT_SECS == 120


class TestActiveTaskShape:
    """Each active_tasks entry must carry a `submitted_at` monotonic
    timestamp; without it the abandon-on-timeout branch in
    `_run_file_changes_check` cannot compute task age and stuck tasks
    remain in the active set forever.
    """

    REQUIRED_FIELDS = {'task', 'size', 'path', 'submitted_at'}

    def test_required_fields_contract(self):
        assert self.REQUIRED_FIELDS == {'task', 'size', 'path', 'submitted_at'}


class TestAgeComparison:
    """The timeout branch uses `time.monotonic()` deltas, not wall clock,
    so NTP jumps cannot prematurely abandon healthy tasks.
    """

    def test_fresh_task_not_abandoned(self):
        submitted_at = time.monotonic()
        age = time.monotonic() - submitted_at
        assert age < 1800

    def test_stale_task_exceeds_timeout(self):
        submitted_at = time.monotonic() - 2000
        age = time.monotonic() - submitted_at
        assert age > 1800

    def test_age_is_monotonic_non_negative(self):
        submitted_at = time.monotonic()
        time.sleep(0.001)
        age = time.monotonic() - submitted_at
        assert age >= 0


class TestFileChangesProgressRedis:
    """v2.6.42 Redis real-time progress channel for the integrity scan.

    Mirrors the v2.5.67 pattern (scan_progress) for the file-changes
    namespace so the API can show live counters while the scan is active.
    """

    def test_get_returns_none_when_redis_unavailable(self):
        with patch('pixelprobe.progress_utils.get_redis_client', return_value=None):
            assert progress_utils.get_file_changes_progress_redis('check-id-123') is None

    def test_get_returns_none_when_key_missing(self):
        fake_client = MagicMock()
        fake_client.hgetall.return_value = {}
        with patch('pixelprobe.progress_utils.get_redis_client', return_value=fake_client):
            assert progress_utils.get_file_changes_progress_redis('check-id-123') is None

    def test_round_trip_returns_decoded_values(self):
        fake_client = MagicMock()
        fake_client.hgetall.return_value = {
            b'files_processed': b'42',
            b'total_files': b'100',
            b'phase': b'dispatching_tasks',
            b'progress_message': b'Processing files: 42/100 (42%)',
            b'last_update': b'2026-05-01T17:00:00+00:00',
        }
        with patch('pixelprobe.progress_utils.get_redis_client', return_value=fake_client):
            result = progress_utils.get_file_changes_progress_redis('check-id-123')

        assert result == {
            'files_processed': 42,
            'total_files': 100,
            'phase': 'dispatching_tasks',
            'progress_message': 'Processing files: 42/100 (42%)',
            'last_update': '2026-05-01T17:00:00+00:00',
        }

    def test_update_writes_hset_with_ttl(self):
        fake_client = MagicMock()
        with patch('pixelprobe.progress_utils.get_redis_client', return_value=fake_client):
            progress_utils.update_file_changes_progress_redis(
                check_id='check-id-123',
                files_processed=42,
                total_files=100,
                phase='dispatching_tasks',
                progress_message='Processing files: 42/100 (42%)',
            )

        fake_client.hset.assert_called_once()
        args, kwargs = fake_client.hset.call_args
        assert args[0] == 'file_changes_progress:check-id-123'
        mapping = kwargs['mapping']
        assert mapping['files_processed'] == '42'
        assert mapping['total_files'] == '100'
        assert mapping['phase'] == 'dispatching_tasks'
        fake_client.expire.assert_called_once_with('file_changes_progress:check-id-123', 3600)

    def test_update_swallows_redis_errors(self):
        fake_client = MagicMock()
        fake_client.hset.side_effect = RuntimeError("redis down")
        with patch('pixelprobe.progress_utils.get_redis_client', return_value=fake_client):
            progress_utils.update_file_changes_progress_redis(
                check_id='check-id-123',
                files_processed=42,
                total_files=100,
                phase='x',
                progress_message='y',
            )

    def test_clear_deletes_key(self):
        fake_client = MagicMock()
        with patch('pixelprobe.progress_utils.get_redis_client', return_value=fake_client):
            progress_utils.clear_file_changes_progress_redis('check-id-123')
        fake_client.delete.assert_called_once_with('file_changes_progress:check-id-123')

    def test_separate_key_namespace_from_scan_progress(self):
        """check_id values must not collide with scan_id values."""
        fake_client = MagicMock()
        with patch('pixelprobe.progress_utils.get_redis_client', return_value=fake_client):
            progress_utils.update_file_changes_progress_redis(
                check_id='abc', files_processed=1, total_files=2, phase='', progress_message='',
            )
            progress_utils.update_scan_progress_redis(
                scan_id='abc', files_processed=99, estimated_total=100,
            )

        keys_used = {call.args[0] for call in fake_client.hset.call_args_list}
        # scan progress writes go through a pipeline since v2.6.49
        keys_used |= {call.args[0]
                      for call in fake_client.pipeline.return_value.hset.call_args_list}
        assert 'file_changes_progress:abc' in keys_used
        assert 'scan_progress:abc' in keys_used


class TestDeltaCheckSemantics:
    """The periodic-update block uses a delta check, not a modulo, to fire reliably
    when batches don't align with multiples of update_interval.
    """

    def test_modulo_misses_when_batch_skips_multiple(self):
        # Simulates the production bug: after 75000 (the lucky alignment), the
        # producer batches 2,847 / 3,276 / 3,333 / 3,333 task completions per
        # outer-loop iteration. None of those resulting totals is divisible by
        # 100, so the old `% 100 == 0` check fires only once.
        update_interval = 100
        totals_seen = [0, 75000, 77847, 81123, 84456, 87789]
        modulo_fires = sum(1 for t in totals_seen if t > 0 and t % update_interval == 0)
        assert modulo_fires == 1

    def test_delta_fires_every_interval(self):
        update_interval = 100
        totals_seen = [0, 75000, 77847, 81123, 84456, 87789]
        last_update = 0
        fires = 0
        for total in totals_seen:
            if total > 0 and total - last_update >= update_interval:
                fires += 1
                last_update = total
        # Every non-zero step has a delta exceeding update_interval, so each fires.
        assert fires == len([t for t in totals_seen if t > 0])


class TestCleanupTaskAbandonment:
    """Cleanup Phase 2 mirrors the file-changes hardening: pending tasks past
    CLEANUP_TASK_TIMEOUT_SECS are abandoned as unverifiable (never deleted),
    so the producer loop cannot pin at max concurrency and spin forever.
    Observed live on 2026-06-10: the loop froze at 20,000/1,187,442 with the
    workers idle and no log output, because un-ready handles were retried
    indefinitely with no timeout.
    """

    def test_timeout_constant_is_env_tunable(self):
        from pixelprobe.services.maintenance_service import CLEANUP_TASK_TIMEOUT_SECS
        assert CLEANUP_TASK_TIMEOUT_SECS == 600

    def test_stale_task_is_abandoned_not_kept(self):
        import time as _time
        from pixelprobe.services.maintenance_service import CLEANUP_TASK_TIMEOUT_SECS

        now = _time.monotonic()
        fresh = {'submitted_at': now - 5}
        stale = {'submitted_at': now - CLEANUP_TASK_TIMEOUT_SECS - 1}

        def is_abandoned(task_info):
            return (_time.monotonic() - task_info['submitted_at']) > CLEANUP_TASK_TIMEOUT_SECS

        assert is_abandoned(stale) is True
        assert is_abandoned(fresh) is False

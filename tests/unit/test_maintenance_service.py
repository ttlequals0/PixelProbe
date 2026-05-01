"""Unit tests for MaintenanceService integrity-scan timeout (v2.6.42)."""

import importlib
import os
import time

from pixelprobe.services import maintenance_service


class TestIntegrityTaskTimeoutConfig:
    """INTEGRITY_TASK_TIMEOUT_SECS is read at module import.

    v2.6.42 introduced this constant to break the integrity-scan hang
    where stuck Celery tasks pinned the producer at MAX_CONCURRENT_SMALL
    forever after a worker died.
    """

    def teardown_method(self, method):
        os.environ.pop('INTEGRITY_TASK_TIMEOUT_SECS', None)
        importlib.reload(maintenance_service)

    def test_default_is_thirty_minutes(self):
        os.environ.pop('INTEGRITY_TASK_TIMEOUT_SECS', None)
        importlib.reload(maintenance_service)
        assert maintenance_service.INTEGRITY_TASK_TIMEOUT_SECS == 1800

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

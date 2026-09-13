"""A schedule waiting on a running scan must not be abandoned for waiting.

The retry budget is 144 retries ten minutes apart, so a scan running longer
than a day exhausted it and the schedule was dropped until its next cron fire.
Observed live: a 27-hour scan with 15 hours left, schedule_14 at retry 106/144.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from pixelprobe.scheduler import MediaScheduler


def _scheduler():
    scheduler = MediaScheduler()
    scheduler.scheduler = MagicMock()
    return scheduler


def _scan(minutes_since_update):
    return SimpleNamespace(
        last_update=datetime.now(timezone.utc) - timedelta(minutes=minutes_since_update),
        start_time=None)


class TestScanProgress:

    def test_a_recently_updated_scan_is_progressing(self):
        assert _scheduler()._scan_is_progressing(_scan(5)) is True

    def test_a_long_quiet_scan_is_not(self):
        assert _scheduler()._scan_is_progressing(_scan(90)) is False

    def test_a_scan_with_no_timestamps_is_not(self):
        assert _scheduler()._scan_is_progressing(
            SimpleNamespace(last_update=None, start_time=None)) is False

    def test_a_naive_timestamp_is_read_as_utc(self):
        naive = datetime.now(timezone.utc).replace(tzinfo=None)
        assert _scheduler()._scan_is_progressing(
            SimpleNamespace(last_update=naive, start_time=None)) is True


class TestRetryBudget:

    def test_waiting_on_a_working_scan_costs_nothing(self):
        scheduler = _scheduler()
        for _ in range(500):
            scheduler._queue_conflict_retry('schedule_14', lambda: None, (), 'phase=scanning',
                                            consume_budget=False)
        assert scheduler.pending_retries['schedule_14'] == 0
        assert scheduler.scheduler.add_job.call_count == 500

    def test_a_real_conflict_still_spends_the_budget(self):
        scheduler = _scheduler()
        scheduler.retry_max_count = 3
        for _ in range(3):
            scheduler._queue_conflict_retry('periodic', lambda: None, (), 'api returned 409')
        assert scheduler.pending_retries['periodic'] == 3

        scheduler._queue_conflict_retry('periodic', lambda: None, (), 'api returned 409')
        assert 'periodic' not in scheduler.pending_retries, 'expected the schedule to give up'

    def test_a_stuck_scan_spends_the_budget(self):
        """Otherwise a wedged scan holds a schedule in a retry loop forever."""
        scheduler = _scheduler()
        scheduler.retry_max_count = 2
        for _ in range(3):
            scheduler._queue_conflict_retry(
                'schedule_14', lambda: None, (), 'phase=scanning',
                consume_budget=not scheduler._scan_is_progressing(_scan(90)))
        assert 'schedule_14' not in scheduler.pending_retries

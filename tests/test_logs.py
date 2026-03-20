"""
Test suite for View Logs feature (v2.6.0).

Covers log API routes, DatabaseLogHandler, log context vars,
path filter, and AppConfig model.
"""

import pytest
import time
import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from pixelprobe.models import db, LogEntry, AppConfig, ScanResult
from pixelprobe.utils.log_context import current_scan_id, current_celery_task_id, scan_log_context
from pixelprobe.constants import CONFIG_LOG_RETENTION_DAYS, CONFIG_LOG_EXCLUDE_LOGGERS, SYSTEM_LOG_ID


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def log_client(client, app):
    """Client with log_entries and app_configs tables ready."""
    with app.app_context():
        db.create_all()
        # Clear test data
        LogEntry.query.delete()
        AppConfig.query.delete()
        db.session.commit()
    return client


@pytest.fixture
def auth_log_client(authenticated_client, app):
    """Authenticated client with log tables ready."""
    with app.app_context():
        db.create_all()
        LogEntry.query.delete()
        AppConfig.query.delete()
        db.session.commit()
    return authenticated_client


@pytest.fixture
def sample_logs(app):
    """Insert a set of sample log entries for testing."""
    with app.app_context():
        now = datetime.now(timezone.utc)
        entries = [
            LogEntry(scan_id='scan_001', level='INFO', logger_name='pixelprobe.scan',
                     message='Scan started', timestamp=now - timedelta(hours=2)),
            LogEntry(scan_id='scan_001', level='WARNING', logger_name='pixelprobe.scan',
                     message='Slow file detected', timestamp=now - timedelta(hours=1, minutes=50)),
            LogEntry(scan_id='scan_001', level='ERROR', logger_name='pixelprobe.media_checker',
                     message='FFmpeg failed on /test/bad.mp4', traceback='Traceback:\n  File ...',
                     timestamp=now - timedelta(hours=1, minutes=30)),
            LogEntry(scan_id=None, level='INFO', logger_name='pixelprobe.scheduler',
                     message='Scheduler tick', timestamp=now - timedelta(minutes=30)),
            LogEntry(scan_id='scheduled_daily', level='INFO', logger_name='pixelprobe.scan',
                     message='Scheduled scan started', timestamp=now - timedelta(minutes=15)),
            LogEntry(scan_id='scan_001', level='CRITICAL', logger_name='pixelprobe.scan',
                     message='Database connection lost', timestamp=now - timedelta(minutes=5)),
        ]
        db.session.add_all(entries)
        db.session.commit()
        return entries


# ---------------------------------------------------------------------------
# LogEntry model tests
# ---------------------------------------------------------------------------

class TestLogEntryModel:
    def test_create_log_entry(self, app):
        with app.app_context():
            db.create_all()
            entry = LogEntry(
                scan_id='test_scan',
                celery_task_id='task_123',
                level='ERROR',
                logger_name='pixelprobe.test',
                message='Test error message',
                traceback='Traceback line 1\nTraceback line 2'
            )
            db.session.add(entry)
            db.session.commit()

            fetched = LogEntry.query.first()
            assert fetched.scan_id == 'test_scan'
            assert fetched.celery_task_id == 'task_123'
            assert fetched.level == 'ERROR'
            assert fetched.message == 'Test error message'
            assert fetched.traceback is not None

            LogEntry.query.delete()
            db.session.commit()

    def test_to_dict(self, app):
        with app.app_context():
            db.create_all()
            entry = LogEntry(
                scan_id='scan_1',
                level='INFO',
                logger_name='test',
                message='hello',
                timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
            )
            d = entry.to_dict()
            assert d['scan_id'] == 'scan_1'
            assert d['level'] == 'INFO'
            assert d['message'] == 'hello'
            assert '2026-01-01' in d['timestamp']


# ---------------------------------------------------------------------------
# AppConfig model tests
# ---------------------------------------------------------------------------

class TestAppConfigModel:
    def test_create_and_retrieve(self, app):
        with app.app_context():
            db.create_all()
            config = AppConfig(
                key='test_key',
                value='test_value',
                description='A test config'
            )
            db.session.add(config)
            db.session.commit()

            fetched = AppConfig.query.filter_by(key='test_key').first()
            assert fetched is not None
            assert fetched.value == 'test_value'
            assert fetched.created_at is not None
            assert fetched.updated_at is not None

            AppConfig.query.delete()
            db.session.commit()

    def test_unique_key_constraint(self, app):
        with app.app_context():
            db.create_all()
            c1 = AppConfig(key='unique_key', value='v1')
            db.session.add(c1)
            db.session.commit()

            c2 = AppConfig(key='unique_key', value='v2')
            db.session.add(c2)
            with pytest.raises(Exception):
                db.session.commit()
            db.session.rollback()

            AppConfig.query.delete()
            db.session.commit()

    def test_to_dict(self, app):
        with app.app_context():
            db.create_all()
            config = AppConfig(key='k', value='v', description='desc')
            d = config.to_dict()
            assert d['key'] == 'k'
            assert d['value'] == 'v'
            assert d['description'] == 'desc'


# ---------------------------------------------------------------------------
# Log context var tests
# ---------------------------------------------------------------------------

class TestLogContext:
    def test_context_vars_default_none(self):
        assert current_scan_id.get(None) is None
        assert current_celery_task_id.get(None) is None

    def test_scan_log_context_manager(self):
        with scan_log_context('scan_abc', 'task_xyz'):
            assert current_scan_id.get() == 'scan_abc'
            assert current_celery_task_id.get() == 'task_xyz'
        # After context exits, values should be reset
        assert current_scan_id.get(None) is None
        assert current_celery_task_id.get(None) is None

    def test_nested_context(self):
        with scan_log_context('outer', 'task_outer'):
            assert current_scan_id.get() == 'outer'
            with scan_log_context('inner', 'task_inner'):
                assert current_scan_id.get() == 'inner'
            assert current_scan_id.get() == 'outer'
        assert current_scan_id.get(None) is None

    def test_manual_set_reset(self):
        token = current_scan_id.set('manual_scan')
        assert current_scan_id.get() == 'manual_scan'
        current_scan_id.reset(token)
        assert current_scan_id.get(None) is None


# ---------------------------------------------------------------------------
# DatabaseLogHandler tests
# ---------------------------------------------------------------------------

class TestDatabaseLogHandler:
    def test_handler_init_and_shutdown(self, app):
        from pixelprobe.utils.log_handler import DatabaseLogHandler
        handler = DatabaseLogHandler(app)
        assert handler._running is True
        assert handler._writer_thread.is_alive()
        handler.shutdown()
        assert handler._running is False

    def test_is_excluded(self, app):
        from pixelprobe.utils.log_handler import DatabaseLogHandler
        handler = DatabaseLogHandler(app)
        try:
            # Default excludes include urllib3, werkzeug, etc.
            assert handler._is_excluded('urllib3') is True
            assert handler._is_excluded('urllib3.connectionpool') is True
            assert handler._is_excluded('werkzeug') is True
            assert handler._is_excluded('pixelprobe.scan') is False
            assert handler._is_excluded('pixelprobe.utils.log_handler') is False  # checked separately in emit
        finally:
            handler.shutdown()

    def test_self_recursion_guard(self, app):
        from pixelprobe.utils.log_handler import DatabaseLogHandler
        handler = DatabaseLogHandler(app)
        try:
            record = logging.LogRecord(
                name='pixelprobe.utils.log_handler',
                level=logging.INFO,
                pathname='',
                lineno=0,
                msg='should be ignored',
                args=(),
                exc_info=None
            )
            # Should not raise and should not queue
            handler.emit(record)
            assert handler._queue.empty()
        finally:
            handler.shutdown()

    def test_emit_queues_record(self, app):
        from pixelprobe.utils.log_handler import DatabaseLogHandler
        handler = DatabaseLogHandler(app)
        try:
            record = logging.LogRecord(
                name='pixelprobe.test',
                level=logging.ERROR,
                pathname='',
                lineno=0,
                msg='test error message',
                args=(),
                exc_info=None
            )
            handler.emit(record)
            assert not handler._queue.empty()
            entry = handler._queue.get_nowait()
            assert entry['level'] == 'ERROR'
            assert entry['message'] == 'test error message'
            assert entry['logger_name'] == 'pixelprobe.test'
        finally:
            handler.shutdown()

    def test_emit_captures_context_vars(self, app):
        from pixelprobe.utils.log_handler import DatabaseLogHandler
        handler = DatabaseLogHandler(app)
        try:
            with scan_log_context('ctx_scan', 'ctx_task'):
                record = logging.LogRecord(
                    name='pixelprobe.test',
                    level=logging.INFO,
                    pathname='',
                    lineno=0,
                    msg='context test',
                    args=(),
                    exc_info=None
                )
                handler.emit(record)
            entry = handler._queue.get_nowait()
            assert entry['scan_id'] == 'ctx_scan'
            assert entry['celery_task_id'] == 'ctx_task'
        finally:
            handler.shutdown()

    def test_excluded_logger_not_queued(self, app):
        from pixelprobe.utils.log_handler import DatabaseLogHandler
        handler = DatabaseLogHandler(app)
        try:
            record = logging.LogRecord(
                name='werkzeug',
                level=logging.INFO,
                pathname='',
                lineno=0,
                msg='should be excluded',
                args=(),
                exc_info=None
            )
            handler.emit(record)
            assert handler._queue.empty()
        finally:
            handler.shutdown()


# ---------------------------------------------------------------------------
# Log API route tests
# ---------------------------------------------------------------------------

class TestGetLogs:
    def test_unauthenticated_returns_401(self, log_client):
        resp = log_client.get('/api/logs')
        assert resp.status_code in (401, 302)  # 302 redirect to login or 401

    def test_get_logs_empty(self, auth_log_client, app):
        resp = auth_log_client.get('/api/logs')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['logs'] == []
        assert data['total'] == 0

    def test_get_logs_with_data(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.get('/api/logs')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 6
        assert len(data['logs']) == 6

    def test_filter_by_level(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.get('/api/logs?level=ERROR')
        assert resp.status_code == 200
        data = resp.get_json()
        # ERROR and CRITICAL only
        for log in data['logs']:
            assert log['level'] in ('ERROR', 'CRITICAL')

    def test_filter_by_scan_id(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.get('/api/logs?scan_id=scan_001')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 4
        for log in data['logs']:
            assert log['scan_id'] == 'scan_001'

    def test_filter_by_system(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.get('/api/logs?scan_id=system')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 1
        assert data['logs'][0]['scan_id'] is None

    def test_search_filter(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.get('/api/logs?search=FFmpeg')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 1
        assert 'FFmpeg' in data['logs'][0]['message']

    def test_search_escapes_wildcards(self, auth_log_client, app):
        """Verify that LIKE wildcards in search are escaped."""
        with app.app_context():
            entry = LogEntry(level='INFO', logger_name='test',
                             message='100% complete',
                             timestamp=datetime.now(timezone.utc))
            db.session.add(entry)
            db.session.commit()

        resp = auth_log_client.get('/api/logs?search=100%25')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 1

    def test_pagination(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.get('/api/logs?per_page=2&page=1')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['logs']) == 2
        assert data['total'] == 6
        assert data['has_more'] is True

    def test_per_page_max_1000(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.get('/api/logs?per_page=5000')
        assert resp.status_code == 200
        # Should cap at 1000, not error

    def test_polling_with_since(self, auth_log_client, app, sample_logs):
        # Use a timestamp that's older than some entries
        since = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        resp = auth_log_client.get(f'/api/logs?since={since}')
        assert resp.status_code == 200
        data = resp.get_json()
        # Should return entries newer than 20 minutes ago
        assert data['total'] > 0


class TestGetLogRuns:
    def test_get_runs(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.get('/api/logs/runs')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'runs' in data
        # Should have scan_001, scheduled_daily, and system (None)
        run_ids = [r['scan_id'] for r in data['runs']]
        assert 'scan_001' in run_ids
        assert 'scheduled_daily' in run_ids
        assert SYSTEM_LOG_ID in run_ids

    def test_filter_by_scan_type(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.get('/api/logs/runs?scan_type=scheduled')
        assert resp.status_code == 200
        data = resp.get_json()
        for run in data['runs']:
            assert run['scan_type'] == 'scheduled'


class TestDownloadLogs:
    def test_download(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.get('/api/logs/download')
        assert resp.status_code == 200
        assert resp.content_type == 'text/plain; charset=utf-8'
        assert 'attachment' in resp.headers.get('Content-Disposition', '')
        content = resp.data.decode()
        assert 'Scan started' in content

    def test_download_with_filter(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.get('/api/logs/download?level=ERROR')
        assert resp.status_code == 200
        content = resp.data.decode()
        assert 'FFmpeg' in content
        # INFO-level messages should not appear
        assert 'Scan started' not in content

    def test_download_includes_traceback(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.get('/api/logs/download?level=ERROR')
        content = resp.data.decode()
        assert 'Traceback:' in content


class TestLogRetention:
    def test_get_default_retention(self, auth_log_client, app):
        resp = auth_log_client.get('/api/logs/retention')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['log_retention_days'] == 30

    def test_set_retention(self, auth_log_client, app):
        resp = auth_log_client.put('/api/logs/retention',
                                   json={'log_retention_days': 60})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['log_retention_days'] == 60

        # Verify it persisted
        resp2 = auth_log_client.get('/api/logs/retention')
        assert resp2.get_json()['log_retention_days'] == 60

    def test_set_invalid_retention(self, auth_log_client, app):
        resp = auth_log_client.put('/api/logs/retention',
                                   json={'log_retention_days': -1})
        assert resp.status_code == 400

    def test_set_non_integer_retention(self, auth_log_client, app):
        resp = auth_log_client.put('/api/logs/retention',
                                   json={'log_retention_days': 'thirty'})
        assert resp.status_code == 400

    def test_set_missing_retention(self, auth_log_client, app):
        resp = auth_log_client.put('/api/logs/retention', json={})
        assert resp.status_code == 400


class TestPurgeLogs:
    def test_purge_requires_filter(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.post('/api/logs/purge', json={})
        assert resp.status_code == 400
        assert 'filter' in resp.get_json()['error'].lower()

    def test_purge_by_scan_id(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.post('/api/logs/purge', json={'scan_id': 'scan_001'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['deleted'] == 4

        # Verify remaining logs
        with app.app_context():
            remaining = LogEntry.query.count()
            assert remaining == 2

    def test_purge_by_before(self, auth_log_client, app, sample_logs):
        before = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = auth_log_client.post('/api/logs/purge', json={'before': before})
        assert resp.status_code == 200
        assert resp.get_json()['deleted'] >= 1

    def test_purge_invalid_before(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.post('/api/logs/purge', json={'before': 'not-a-date'})
        assert resp.status_code == 400

    def test_purge_by_level(self, auth_log_client, app, sample_logs):
        resp = auth_log_client.post('/api/logs/purge', json={'level': 'CRITICAL'})
        assert resp.status_code == 200
        assert resp.get_json()['deleted'] == 1

    def test_purge_ignores_undocumented_filters(self, auth_log_client, app, sample_logs):
        """Purge should only accept scan_id, before, level -- not search/start_time/end_time."""
        resp = auth_log_client.post('/api/logs/purge',
                                    json={'level': 'INFO', 'search': 'Scan started'})
        assert resp.status_code == 200
        data = resp.get_json()
        # search should be ignored, so all INFO+ logs are purged (all 6)
        assert data['deleted'] == 6


# ---------------------------------------------------------------------------
# Path filter tests
# ---------------------------------------------------------------------------

class TestPathFilter:
    def test_get_scan_paths(self, auth_log_client, app):
        with app.app_context():
            from pixelprobe.models import ScanConfiguration
            db.create_all()
            ScanConfiguration.query.delete()
            config = ScanConfiguration(path='/media/movies', is_active=True,
                                       created_at=datetime.now(timezone.utc))
            db.session.add(config)
            db.session.commit()

        resp = auth_log_client.get('/api/scan-paths')
        assert resp.status_code == 200
        data = resp.get_json()
        assert '/media/movies' in data['paths']

    def test_path_filter_valid_path(self, auth_log_client, app):
        """Path filter should return results matching the configured path."""
        with app.app_context():
            from pixelprobe.models import ScanConfiguration
            db.create_all()
            ScanConfiguration.query.delete()
            config = ScanConfiguration(path='/media/movies', is_active=True,
                                       created_at=datetime.now(timezone.utc))
            db.session.add(config)

            # Add scan results under that path
            r1 = ScanResult(file_path='/media/movies/test.mp4', file_size=1024,
                            file_type='video/mp4', scan_date=datetime.now(timezone.utc),
                            scan_status='completed', is_corrupted=False,
                            file_hash='hash1', marked_as_good=False)
            r2 = ScanResult(file_path='/media/tv/show.mp4', file_size=2048,
                            file_type='video/mp4', scan_date=datetime.now(timezone.utc),
                            scan_status='completed', is_corrupted=False,
                            file_hash='hash2', marked_as_good=False)
            db.session.add_all([r1, r2])
            db.session.commit()

        resp = auth_log_client.get('/api/scan-results?path=/media/movies')
        assert resp.status_code == 200
        data = resp.get_json()
        for result in data['results']:
            assert result['file_path'].startswith('/media/movies')

    def test_path_filter_invalid_path(self, auth_log_client, app):
        """Non-configured paths should return empty results."""
        with app.app_context():
            from pixelprobe.models import ScanConfiguration
            db.create_all()
            ScanConfiguration.query.delete()
            config = ScanConfiguration(path='/media/movies', is_active=True,
                                       created_at=datetime.now(timezone.utc))
            db.session.add(config)
            db.session.commit()

        resp = auth_log_client.get('/api/scan-results?path=/etc/passwd')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['results'] == []
        assert data['total'] == 0


# ---------------------------------------------------------------------------
# Maintenance: cleanup_old_logs
# ---------------------------------------------------------------------------

class TestLogCleanup:
    def test_cleanup_old_logs(self, app):
        from pixelprobe.services.maintenance_service import MaintenanceService
        with app.app_context():
            db.create_all()
            LogEntry.query.delete()

            # Set retention to 1 day
            config = AppConfig.query.filter_by(key=CONFIG_LOG_RETENTION_DAYS).first()
            if config:
                config.value = '1'
            else:
                db.session.add(AppConfig(key=CONFIG_LOG_RETENTION_DAYS, value='1'))
            db.session.commit()

            # Insert old and new logs
            old_entry = LogEntry(level='INFO', logger_name='test', message='old',
                                 timestamp=datetime.now(timezone.utc) - timedelta(days=5))
            new_entry = LogEntry(level='INFO', logger_name='test', message='new',
                                 timestamp=datetime.now(timezone.utc))
            db.session.add_all([old_entry, new_entry])
            db.session.commit()

            deleted = MaintenanceService.cleanup_old_logs()
            assert deleted == 1
            assert LogEntry.query.count() == 1
            assert LogEntry.query.first().message == 'new'

            # Cleanup
            LogEntry.query.delete()
            AppConfig.query.delete()
            db.session.commit()

    def test_cleanup_default_retention(self, app):
        """Without AppConfig, should default to 30 days."""
        from pixelprobe.services.maintenance_service import MaintenanceService
        with app.app_context():
            db.create_all()
            LogEntry.query.delete()
            AppConfig.query.delete()

            # Insert a log 31 days old
            old_entry = LogEntry(level='INFO', logger_name='test', message='very old',
                                 timestamp=datetime.now(timezone.utc) - timedelta(days=31))
            db.session.add(old_entry)
            db.session.commit()

            deleted = MaintenanceService.cleanup_old_logs()
            assert deleted == 1

            LogEntry.query.delete()
            db.session.commit()

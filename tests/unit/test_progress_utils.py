"""
Unit tests for progress_utils and ScanService completion helpers
"""

import os
import sys
import json
from types import ModuleType
os.environ.setdefault('SECRET_KEY', 'test-secret-key')

import pytest
from unittest.mock import Mock, patch, MagicMock
from pixelprobe.models import (db, ScanNotificationOutbox, ScanReport, ScanRunFile,
                               ScanRunRoot, ScanState)


class TestGetScanProgressRedis:
    """Test get_scan_progress_redis function"""

    @patch('pixelprobe.progress_utils.get_redis_client')
    def test_returns_none_when_no_redis_client(self, mock_get_client):
        """Should return None when Redis client is unavailable"""
        from pixelprobe.progress_utils import get_scan_progress_redis
        mock_get_client.return_value = None

        result = get_scan_progress_redis('scan-123')
        assert result is None

    @patch('pixelprobe.progress_utils.get_redis_client')
    def test_returns_none_when_no_data(self, mock_get_client):
        """Should return None when Redis has no data for scan"""
        from pixelprobe.progress_utils import get_scan_progress_redis
        mock_client = Mock()
        mock_client.hgetall.return_value = {}
        mock_get_client.return_value = mock_client

        result = get_scan_progress_redis('scan-123')
        assert result is None

    @patch('pixelprobe.progress_utils.get_redis_client')
    def test_decodes_bytes_correctly(self, mock_get_client):
        """Should decode Redis byte responses to proper types"""
        from pixelprobe.progress_utils import get_scan_progress_redis
        mock_client = Mock()
        mock_client.hgetall.return_value = {
            b'files_processed': b'42',
            b'estimated_total': b'100',
            b'phase': b'scanning',
            b'current_file': b'/media/test.mp4',
            b'last_update': b'2025-01-01T00:00:00+00:00',
        }
        mock_get_client.return_value = mock_client

        result = get_scan_progress_redis('scan-123')

        assert result['files_processed'] == 42
        assert result['estimated_total'] == 100
        assert result['phase'] == 'scanning'
        assert result['current_file'] == '/media/test.mp4'
        assert result['last_update'] == '2025-01-01T00:00:00+00:00'

    @patch('pixelprobe.progress_utils.get_redis_client')
    def test_handles_string_keys(self, mock_get_client):
        """Should handle non-byte (string) keys/values from Redis"""
        from pixelprobe.progress_utils import get_scan_progress_redis
        mock_client = Mock()
        mock_client.hgetall.return_value = {
            'files_processed': '10',
            'estimated_total': '50',
            'phase': 'discovering',
            'current_file': '',
            'last_update': '',
        }
        mock_get_client.return_value = mock_client

        result = get_scan_progress_redis('scan-123')

        assert result['files_processed'] == 10
        assert result['estimated_total'] == 50
        assert result['phase'] == 'discovering'

    @patch('pixelprobe.progress_utils.get_redis_client')
    def test_defaults_missing_numeric_fields_to_zero(self, mock_get_client):
        """Should default missing numeric fields to 0"""
        from pixelprobe.progress_utils import get_scan_progress_redis
        mock_client = Mock()
        mock_client.hgetall.return_value = {
            b'phase': b'scanning',
        }
        mock_get_client.return_value = mock_client

        result = get_scan_progress_redis('scan-123')

        assert result['files_processed'] == 0
        assert result['estimated_total'] == 0

    @patch('pixelprobe.progress_utils.get_redis_client')
    def test_returns_none_on_exception(self, mock_get_client):
        """Should return None on Redis errors"""
        from pixelprobe.progress_utils import get_scan_progress_redis
        mock_client = Mock()
        mock_client.hgetall.side_effect = Exception("Connection refused")
        mock_get_client.return_value = mock_client

        result = get_scan_progress_redis('scan-123')
        assert result is None

    @patch('pixelprobe.progress_utils.get_redis_client')
    def test_reads_bounded_active_files(self, mock_get_client):
        from pixelprobe.progress_utils import get_scan_progress_redis
        mock_client = Mock()
        active_files = [
            {'file': f'/media/{index}.mp4', 'directory': '/media'}
            for index in range(10)
        ]
        mock_client.hgetall.return_value = {
            b'active_files': json.dumps(active_files).encode(),
            b'active_file_count': b'10',
        }
        mock_get_client.return_value = mock_client

        result = get_scan_progress_redis('scan-123')

        assert len(result['active_files']) == 8
        assert result['active_file_count'] == 10
        assert result['active_files_truncated'] is True



class TestMarkScanCompleted:
    """Test ScanService._mark_scan_completed"""

    @staticmethod
    def _stub_outbox_dispatch(monkeypatch):
        task_module = ModuleType('pixelprobe.tasks')
        task_module.deliver_scan_notification_outbox = Mock()
        monkeypatch.setitem(sys.modules, 'pixelprobe.tasks', task_module)
        return task_module.deliver_scan_notification_outbox.apply_async

    def _create_complete_run(self, app, scan_id):
        with app.app_context():
            state = ScanState(scan_id=scan_id, is_active=True, phase='scanning',
                              estimated_total=1, files_processed=0)
            db.session.add(state)
            db.session.add(ScanRunRoot(scan_id=scan_id, root_path='/media',
                                       status='completed', discovered_count=1))
            db.session.add(ScanRunFile(scan_id=scan_id, file_path='/media/one.mp4',
                                       status='completed', outcome='completed'))
            db.session.commit()
            return state.id

    def test_finalizes_run_with_atomic_report_and_outbox(self, app, db, monkeypatch):
        """Completion persists terminal state, report, and durable outbox together."""
        from pixelprobe.services.scan_service import ScanService
        service = ScanService(':memory:')
        mock_dispatch = self._stub_outbox_dispatch(monkeypatch)
        scan_id = 'progress-finalize-success'
        state_id = self._create_complete_run(app, scan_id)

        with app.app_context():
            service._mark_scan_completed(scan_state_id=state_id, files_processed=1,
                                         estimated_total=1)
            state = db.session.get(ScanState, state_id)
            report = ScanReport.query.filter_by(scan_id=scan_id).one()
            outbox = ScanNotificationOutbox.query.filter_by(scan_id=scan_id).one()

        assert state.phase == 'completed'
        assert state.is_active is False
        assert state.files_processed == 1
        assert state.phase_total == 1
        assert report.status == 'completed'
        assert report.files_scanned == 1
        assert outbox.event == 'scan_completed'
        assert outbox.targets_initialized is True
        mock_dispatch.assert_called_once_with(args=(outbox.id,))

    def test_completion_requires_complete_root_and_member_evidence(
            self, app, db, monkeypatch):
        """An unavailable root is terminal evidence of an error, not a healthy run."""
        from pixelprobe.services.scan_service import ScanService
        service = ScanService(':memory:')
        mock_dispatch = self._stub_outbox_dispatch(monkeypatch)
        scan_id = 'progress-finalize-unavailable-root'
        state_id = self._create_complete_run(app, scan_id)

        with app.app_context():
            root = ScanRunRoot.query.filter_by(scan_id=scan_id).one()
            root.status = 'unavailable'
            db.session.commit()
            service._mark_scan_completed(scan_state_id=state_id, files_processed=1,
                                         estimated_total=1)
            state = db.session.get(ScanState, state_id)
            report = ScanReport.query.filter_by(scan_id=scan_id).one()
            outbox = ScanNotificationOutbox.query.filter_by(scan_id=scan_id).one()

        assert state.phase == 'error'
        assert state.is_active is False
        assert 'roots were not completely observed' in state.error_message
        assert report.status == 'error'
        assert outbox.event == 'scan_completed'
        mock_dispatch.assert_called_once_with(args=(outbox.id,))

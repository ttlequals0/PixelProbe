"""
Unit tests for progress_utils and ScanService completion helpers
"""

import os
os.environ.setdefault('SECRET_KEY', 'test-secret-key')

import pytest
from unittest.mock import Mock, patch, MagicMock


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



class TestMarkScanCompleted:
    """Test ScanService._mark_scan_completed"""

    @patch('pixelprobe.services.scan_service.db')
    def test_executes_sql_update(self, mock_db, app):
        """Should execute SQL UPDATE with correct params and commit"""
        from pixelprobe.services.scan_service import ScanService
        service = ScanService(':memory:')

        with app.app_context():
            service._mark_scan_completed(scan_state_id=42, files_processed=100, estimated_total=200)

        mock_db.session.execute.assert_called_once()
        call_args = mock_db.session.execute.call_args
        params = call_args[0][1]
        assert params['id'] == 42
        assert params['files_processed'] == 100
        assert params['estimated_total'] == 200
        assert 'end_time' in params
        mock_db.session.commit.assert_called_once()

    @patch('pixelprobe.services.scan_service.db')
    def test_sql_sets_phase_completed(self, mock_db, app):
        """Should set phase to completed and is_active to false"""
        from pixelprobe.services.scan_service import ScanService
        service = ScanService(':memory:')

        with app.app_context():
            service._mark_scan_completed(scan_state_id=1, files_processed=0, estimated_total=0)

        call_args = mock_db.session.execute.call_args
        sql_text = str(call_args[0][0])
        assert 'completed' in sql_text
        assert 'is_active' in sql_text

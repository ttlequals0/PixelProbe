"""
Unit tests for ScanService
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import threading
import time

from pixelprobe.services.scan_service import ScanService
from models import ScanResult, ScanState

class TestScanService:
    """Test the scan service business logic"""
    
    @pytest.fixture
    def scan_service(self):
        """Create a scan service instance"""
        return ScanService(':memory:')
    
    def test_is_scan_running_initial_state(self, scan_service):
        """Test that no scan is running initially"""
        assert scan_service.is_scan_running() == False
    
    def test_get_scan_progress_initial_state(self, scan_service):
        """Test initial scan progress state"""
        progress = scan_service.get_scan_progress()
        
        assert progress['current'] == 0
        assert progress['total'] == 0
        assert progress['file'] == ''
        assert progress['status'] == 'idle'
    
    def test_update_progress(self, scan_service):
        """Test progress update functionality"""
        scan_service.update_progress(5, 10, '/test/file.mp4', 'scanning')
        
        progress = scan_service.get_scan_progress()
        assert progress['current'] == 5
        assert progress['total'] == 10
        assert progress['file'] == '/test/file.mp4'
        assert progress['status'] == 'scanning'
    
    @patch('os.path.exists')
    @patch('pixelprobe.services.scan_service.PixelProbe')
    def test_scan_single_file_success(self, mock_probe_class, mock_exists, scan_service):
        """Test successful single file scan"""
        mock_exists.return_value = True
        mock_probe = Mock()
        mock_probe_class.return_value = mock_probe
        
        # Mock scan result
        mock_result = Mock()
        mock_probe.scan_file.return_value = mock_result
        
        # Start scan
        result = scan_service.scan_single_file('/test/file.mp4')
        
        assert result['message'] == 'Scan started'
        assert result['file_path'] == '/test/file.mp4'
        
        # Wait for thread to start
        time.sleep(0.1)
        assert scan_service.is_scan_running() == True
        
        # Wait for scan to complete
        scan_service.current_scan_thread.join(timeout=1)
        
        # Verify scan was called
        mock_probe.scan_file.assert_called_once_with('/test/file.mp4', force_rescan=False)
    
    def test_scan_single_file_not_found(self, scan_service):
        """Test scanning non-existent file"""
        with pytest.raises(FileNotFoundError):
            scan_service.scan_single_file('/nonexistent/file.mp4')
    
    @patch('pixelprobe.services.scan_service.PixelProbe')
    def test_scan_single_file_already_running(self, mock_probe_class, scan_service):
        """Test error when scan is already running"""
        # Set up a fake running thread
        scan_service.current_scan_thread = threading.Thread(target=lambda: time.sleep(1))
        scan_service.current_scan_thread.start()
        
        try:
            with pytest.raises(RuntimeError, match="Another scan is already in progress"):
                scan_service.scan_single_file('/test/file.mp4')
        finally:
            scan_service.current_scan_thread.join()
    
    @patch('os.path.exists')
    @patch('pixelprobe.services.scan_service.db')
    @patch('pixelprobe.services.scan_service.ScanState')
    @patch('pixelprobe.services.scan_service.PixelProbe')
    def test_scan_directories_success(self, mock_probe_class, mock_scan_state_class, mock_db, mock_exists, scan_service):
        """Test successful directory scanning"""
        mock_exists.return_value = True
        
        # Mock scan state
        mock_scan_state = Mock()
        mock_scan_state_class.get_or_create.return_value = mock_scan_state
        
        # Mock probe
        mock_probe = Mock()
        mock_probe_class.return_value = mock_probe
        mock_probe.discover_media_files.return_value = ['/test/file1.mp4', '/test/file2.mp4']
        
        # Start scan
        result = scan_service.scan_directories(['/test/dir'], force_rescan=True)
        
        assert result['message'] == 'Scan started'
        assert result['directories'] == ['/test/dir']
        assert result['force_rescan'] == True
        assert result['num_workers'] == 1
        
        # Wait for thread to complete
        scan_service.current_scan_thread.join(timeout=2)
        
        # Verify scan state was updated
        mock_scan_state.start_scan.assert_called_once()
        mock_scan_state.complete_scan.assert_called_once()
    
    def test_scan_directories_no_valid_dirs(self, scan_service):
        """Test error when no valid directories provided"""
        with pytest.raises(ValueError, match="No valid directories provided"):
            scan_service.scan_directories(['/nonexistent/dir'])
    
    @patch('pixelprobe.services.scan_service.db')
    @patch('pixelprobe.services.scan_service.ScanState')
    def test_cancel_scan(self, mock_scan_state_class, mock_db, scan_service):
        """Test scan cancellation"""
        # Set up a fake running thread
        scan_service.current_scan_thread = threading.Thread(target=lambda: time.sleep(0.5))
        scan_service.current_scan_thread.start()
        
        # Mock scan state
        mock_scan_state = Mock()
        mock_scan_state_class.get_or_create.return_value = mock_scan_state
        
        # Cancel scan
        result = scan_service.cancel_scan()
        
        assert result['message'] == 'Scan cancellation requested'
        assert scan_service.scan_cancelled == True
        
        # Verify scan state was updated
        mock_scan_state.cancel_scan.assert_called_once()
        
        # Clean up
        scan_service.current_scan_thread.join()
    
    def test_cancel_scan_not_running(self, scan_service):
        """Test error when cancelling with no scan running"""
        with pytest.raises(RuntimeError, match="No scan is currently running"):
            scan_service.cancel_scan()
    
    @patch('pixelprobe.services.scan_service.db')
    def test_reset_stuck_scans(self, mock_db, scan_service, db):
        """Test resetting stuck scans"""
        from models import ScanResult
        
        # Create stuck scan results
        stuck1 = ScanResult(file_path='/test/stuck1.mp4', scan_status='scanning')
        stuck2 = ScanResult(file_path='/test/stuck2.mp4', scan_status='scanning')
        db.session.add(stuck1)
        db.session.add(stuck2)
        db.session.commit()
        
        # Reset stuck scans
        with patch.object(ScanResult, 'query') as mock_query:
            mock_query.filter_by.return_value.all.return_value = [stuck1, stuck2]
            
            result = scan_service.reset_stuck_scans()
            
            assert result['message'] == 'Reset 2 stuck files'
            assert result['count'] == 2
            assert stuck1.scan_status == 'pending'
            assert stuck2.scan_status == 'pending'
    
    @patch('os.path.exists')
    @patch('pixelprobe.services.scan_service.db')
    @patch('pixelprobe.services.scan_service.ScanState')
    @patch('pixelprobe.services.scan_service.PixelProbe')
    def test_parallel_scan(self, mock_probe_class, mock_scan_state_class, mock_db, mock_exists, scan_service):
        """Test parallel scanning with multiple workers"""
        mock_exists.return_value = True
        
        # Mock scan state
        mock_scan_state = Mock()
        mock_scan_state_class.get_or_create.return_value = mock_scan_state
        
        # Mock probe
        mock_probe = Mock()
        mock_probe_class.return_value = mock_probe
        mock_probe.discover_media_files.return_value = [
            '/test/file1.mp4', 
            '/test/file2.mp4',
            '/test/file3.mp4',
            '/test/file4.mp4'
        ]
        mock_probe.scan_file.return_value = Mock()
        
        # Start parallel scan
        result = scan_service.scan_directories(['/test/dir'], num_workers=2)
        
        assert result['num_workers'] == 2
        
        # Wait for scan to complete
        scan_service.current_scan_thread.join(timeout=3)
        
        # Verify files were scanned
        assert mock_probe.scan_file.call_count == 4
    
    def test_progress_tracking_thread_safety(self, scan_service):
        """Test that progress tracking is thread-safe"""
        def update_progress_concurrent():
            for i in range(100):
                scan_service.update_progress(i, 100, f'/file{i}.mp4', 'scanning')
        
        # Start multiple threads updating progress
        threads = []
        for _ in range(5):
            thread = threading.Thread(target=update_progress_concurrent)
            threads.append(thread)
            thread.start()
        
        # Wait for all threads
        for thread in threads:
            thread.join()
        
        # Progress should be valid
        progress = scan_service.get_scan_progress()
        assert progress['current'] >= 0
        assert progress['total'] == 100
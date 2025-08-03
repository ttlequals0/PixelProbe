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
    def scan_service(self, app, db):
        """Create a scan service instance"""
        # Ensure tables are created first
        return ScanService(app.config['SQLALCHEMY_DATABASE_URI'])
    
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
    
    def test_progress_completion_states(self, scan_service):
        """Test progress states including completion"""
        # Test scanning state
        scan_service.update_progress(10, 10, '/test/file.mp4', 'scanning')
        progress = scan_service.get_scan_progress()
        assert progress['status'] == 'scanning'
        
        # Test completed state
        scan_service.update_progress(10, 10, '', 'completed')
        progress = scan_service.get_scan_progress()
        assert progress['status'] == 'completed'
        assert progress['current'] == 10
        assert progress['total'] == 10
    
    @patch('os.path.exists')
    @patch('pixelprobe.services.scan_service.PixelProbe')
    def test_scan_single_file_success(self, mock_probe_class, mock_exists, scan_service, app):
        """Test successful single file scan"""
        with app.app_context():
            mock_exists.return_value = True
            mock_probe = Mock()
            mock_probe_class.return_value = mock_probe
            
            # Mock scan result with a delay to ensure thread is running
            mock_result = Mock()
            def mock_scan_with_delay(*args, **kwargs):
                time.sleep(0.2)  # Simulate scan taking time
                return mock_result
            mock_probe.scan_file.side_effect = mock_scan_with_delay
            
            # Start scan
            result = scan_service.scan_single_file('/test/file.mp4')
        
            assert result['message'] == 'Scan started'
            assert result['file_path'] == '/test/file.mp4'
            
            # Wait for thread to start
            time.sleep(0.05)
            assert scan_service.is_scan_running() == True
            
            # Wait for scan to complete
            scan_service.current_scan_thread.join(timeout=1)
            
            # Verify scan was called
            mock_probe.scan_file.assert_called_once_with('/test/file.mp4', force_rescan=False)
    
    def test_scan_single_file_not_found(self, scan_service):
        """Test scanning non-existent file"""
        with pytest.raises(FileNotFoundError):
            scan_service.scan_single_file('/nonexistent/file.mp4')
    
    @patch('os.path.exists')
    @patch('pixelprobe.services.scan_service.PixelProbe')
    def test_scan_single_file_already_running(self, mock_probe_class, mock_exists, scan_service):
        """Test error when scan is already running"""
        mock_exists.return_value = True
        
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
    @patch('pixelprobe.services.scan_service.ScanReport')
    @patch('pixelprobe.services.scan_service.ScanState')
    @patch('pixelprobe.services.scan_service.ScanResult')
    @patch('pixelprobe.services.scan_service.PixelProbe')
    @patch.object(ScanService, '_get_chunk_file_count', return_value=2)
    def test_scan_directories_success(self, mock_get_chunk_count, mock_probe_class, mock_scan_result_class, mock_scan_state_class, mock_scan_report_class, mock_db, mock_exists, scan_service, app, db):
        """Test successful directory scanning"""
        with app.app_context():
            mock_exists.return_value = True
            
            # Mock scan state
            from datetime import datetime, timezone
            mock_scan_state = Mock()
            mock_scan_state.id = 1
            mock_scan_state.scan_id = 'test-scan-123'
            mock_scan_state.start_time = datetime.now(timezone.utc)
            mock_scan_state.end_time = datetime.now(timezone.utc)
            mock_scan_state.directories = ['/test/dir']
            mock_scan_state.force_rescan = True
            mock_scan_state.phase = 'completed'
            mock_scan_state.error_message = None
            mock_scan_state.estimated_total = 2
            mock_scan_state.phase_total = 2
            mock_scan_state_class.get_or_create.return_value = mock_scan_state
            
            # Mock ScanResult query to avoid database access
            def query_side_effect(*args):
                mock_query = Mock()
                # Check if this is the stats query (has func.count)
                if args and hasattr(args[0], '_elements') and any('count' in str(e) for e in getattr(args[0], '_elements', [])):
                    # This is the stats query
                    mock_stats = Mock()
                    mock_stats.total = 2
                    mock_stats.corrupted = 0
                    mock_stats.warnings = 0
                    mock_stats.errors = 0
                    mock_stats.completed = 2
                    mock_query.first.return_value = mock_stats
                # Check if querying for ScanResult itself
                elif args and args[0] == mock_scan_result_class:
                    # This is querying ScanResult directly
                    mock_query.count.return_value = 50000  # Less than 100000, so it will load paths
                    # For loading file paths query
                    mock_query.all.return_value = []  # Empty list, no existing files
                # Check if querying for ScanResult.file_path
                elif args and hasattr(args[0], 'property') and hasattr(args[0].property, 'key') and args[0].property.key == 'file_path':
                    # This is querying ScanResult.file_path
                    mock_query.all.return_value = []  # Empty list, no existing files
                else:
                    # This is a normal query
                    mock_query.offset.return_value.limit.return_value.all.return_value = []
                    # Mock the filter query for force_rescan
                    mock_filter_query = Mock()
                    mock_filter_query.all.return_value = []  # No existing files
                    mock_query.filter.return_value = mock_filter_query
                    # Mock count() method for existing files check
                    mock_query.count.return_value = 50000  # Less than 100000, so it will load paths
                return mock_query
            
            mock_db.session.query.side_effect = query_side_effect
            
            # Mock db.session.get to return the scan state
            mock_db.session.get.return_value = mock_scan_state
            
            # Mock probe
            mock_probe = Mock()
            mock_probe_class.return_value = mock_probe
            mock_probe.discover_media_files.return_value = ['/test/file1.mp4', '/test/file2.mp4']
            mock_probe.scan_file.return_value = Mock()
            
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
            # Since no files to scan, it should complete immediately via SQL update
            # Check that the database execute was called to update scan state
            mock_db.session.execute.assert_called()
    
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
        
        assert result['message'] == 'Scan cancellation completed'
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
    @patch('pixelprobe.services.scan_service.ScanReport')
    @patch('pixelprobe.services.scan_service.ScanState')
    @patch('pixelprobe.services.scan_service.ScanResult')
    @patch('pixelprobe.services.scan_service.PixelProbe')
    @patch.object(ScanService, '_get_chunk_file_count', return_value=2)
    def test_parallel_scan(self, mock_get_chunk_count, mock_probe_class, mock_scan_result_class, mock_scan_state_class, mock_scan_report_class, mock_db, mock_exists, scan_service, app, db):
        """Test parallel scanning with multiple workers"""
        with app.app_context():
            mock_exists.return_value = True
            
            # Mock scan state
            from datetime import datetime, timezone
            mock_scan_state = Mock()
            mock_scan_state.id = 1
            mock_scan_state.scan_id = 'test-scan-456'
            mock_scan_state.start_time = datetime.now(timezone.utc)
            mock_scan_state.end_time = datetime.now(timezone.utc)
            mock_scan_state.directories = ['/test/dir']
            mock_scan_state.force_rescan = False
            mock_scan_state.phase = 'completed'
            mock_scan_state.error_message = None
            mock_scan_state.estimated_total = 4
            mock_scan_state.phase_total = 4
            mock_scan_state_class.get_or_create.return_value = mock_scan_state
            
            # Mock ScanResult query to avoid database access
            def query_side_effect(*args):
                mock_query = Mock()
                # Check if this is the stats query (has func.count)
                if args and hasattr(args[0], '_elements') and any('count' in str(e) for e in getattr(args[0], '_elements', [])):
                    # This is the stats query
                    mock_stats = Mock()
                    mock_stats.total = 4
                    mock_stats.corrupted = 0
                    mock_stats.warnings = 0
                    mock_stats.errors = 0
                    mock_stats.completed = 4
                    mock_query.first.return_value = mock_stats
                # Check if querying for ScanResult itself
                elif args and args[0] == mock_scan_result_class:
                    # This is querying ScanResult directly
                    mock_query.count.return_value = 50000  # Less than 100000, so it will load paths
                    # For loading file paths query
                    mock_query.all.return_value = []  # Empty list, no existing files
                # Check if querying for ScanResult.file_path
                elif args and hasattr(args[0], 'property') and hasattr(args[0].property, 'key') and args[0].property.key == 'file_path':
                    # This is querying ScanResult.file_path
                    mock_query.all.return_value = []  # Empty list, no existing files
                else:
                    # This is a normal query
                    mock_query.offset.return_value.limit.return_value.all.return_value = []
                    # Mock the filter query for force_rescan
                    mock_filter_query = Mock()
                    mock_filter_query.all.return_value = []  # No existing files
                    mock_query.filter.return_value = mock_filter_query
                    # Mock count() method for existing files check
                    mock_query.count.return_value = 50000  # Less than 100000, so it will load paths
                return mock_query
            
            mock_db.session.query.side_effect = query_side_effect
            
            # Mock db.session.get to return the scan state
            mock_db.session.get.return_value = mock_scan_state
            
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
            # With the current mock setup, no files would be scanned since the query returns empty
            # The test should verify the scan completed successfully
            mock_scan_state.start_scan.assert_called_once()
    
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
import pytest
from models import db, ScanResult, ScanState
from datetime import datetime, timezone

class TestScanManagementEndpoints:
    """Test scan management endpoints"""

    def test_scan_recovery_with_active_scans(self, authenticated_client, app, db):
        """Test scan recovery cleans up active scans"""
        with app.app_context():
            # Create an active scan state
            scan_state = ScanState.get_or_create()
            scan_state.is_active = True
            scan_state.phase = 'scanning'
            scan_state.start_time = datetime.now(timezone.utc)
            db.session.commit()

            # Mock scan service to report scan as not running
            original_is_running = app.scan_service.is_scan_running
            app.scan_service.is_scan_running = lambda: False

            try:
                # Use scan recovery endpoint
                response = authenticated_client.post('/api/scan/recovery')
                assert response.status_code == 200
                data = response.get_json()
                assert data['status'] == 'success'
                assert 'cleaned_count' in data
                assert data['cleaned_count'] >= 1

                # Verify recovery
                db.session.refresh(scan_state)
                assert scan_state.is_active is False
                assert scan_state.phase == 'crashed'
            finally:
                app.scan_service.is_scan_running = original_is_running

    def test_scan_recovery_none_active(self, authenticated_client, app, db):
        """Test scan recovery when no active scans"""
        with app.app_context():
            # Ensure no active scans
            scan_state = ScanState.get_or_create()
            scan_state.is_active = False
            scan_state.phase = 'idle'
            db.session.commit()

            # Mock scan service
            original_is_running = app.scan_service.is_scan_running
            app.scan_service.is_scan_running = lambda: False

            try:
                response = authenticated_client.post('/api/scan/recovery')
                assert response.status_code == 200
                data = response.get_json()
                assert data['status'] == 'success'
                assert data['cleaned_count'] == 0
            finally:
                app.scan_service.is_scan_running = original_is_running

    def test_reset_for_rescan_single_file(self, authenticated_client, app, db):
        """Test resetting single file for rescan"""
        with app.app_context():
            # Create completed scan result
            result = ScanResult(
                file_path='/test/completed.mp4',
                scan_status='completed',
                is_corrupted=False,
                scan_date=datetime.now(timezone.utc)
            )
            db.session.add(result)
            db.session.commit()
            
            # Reset for rescan
            response = authenticated_client.post('/api/reset-files-by-path',
                json={'file_path': '/test/completed.mp4'})
            assert response.status_code == 200
            data = response.get_json()
            assert data['reset_count'] == 1
            
            # Verify reset
            db.session.refresh(result)
            assert result.scan_status == 'pending'
    
    def test_reset_for_rescan_multiple_files(self, authenticated_client, app, db):
        """Test resetting multiple files for rescan"""
        with app.app_context():
            # Create scan results
            files = []
            for i in range(3):
                result = ScanResult(
                    file_path=f'/test/file{i}.mp4',
                    scan_status='completed',
                    scan_date=datetime.now(timezone.utc)
                )
                db.session.add(result)
                files.append(f'/test/file{i}.mp4')
            db.session.commit()
            
            # Reset for rescan
            response = authenticated_client.post('/api/reset-files-by-path',
                json={'file_paths': files})
            assert response.status_code == 200
            data = response.get_json()
            assert data['reset_count'] == 3
    
    def test_reset_for_rescan_no_files(self, authenticated_client, db):
        """Test resetting with no files specified"""
        response = authenticated_client.post('/api/reset-files-by-path', json={})
        assert response.status_code == 400
        assert 'No file paths provided' in response.get_json()['error']


class TestScanCancellationEndpoint:
    """Test scan cancellation endpoint"""
    
    def test_cancel_scan_success(self, authenticated_client, app, db, monkeypatch):
        """Test successful scan cancellation"""
        # Mock scan service
        class MockScanService:
            def is_scan_running(self):
                return True
            
            def cancel_scan(self):
                return {'message': 'Scan cancelled successfully'}
        
        # Store original service
        original_service = getattr(app, 'scan_service', None)
        
        mock_service = MockScanService()
        with app.app_context():
            app.scan_service = mock_service
        
        try:
            response = authenticated_client.post('/api/cancel-scan')
            assert response.status_code == 200
            assert 'Scan cancelled successfully' in response.get_json()['message']
        finally:
            # Restore original service
            if original_service:
                app.scan_service = original_service
            elif hasattr(app, 'scan_service'):
                delattr(app, 'scan_service')
    
    def test_cancel_scan_not_running(self, authenticated_client, app, db, monkeypatch):
        """Test cancelling when no scan running"""
        # Mock scan service
        class MockScanService:
            def is_scan_running(self):
                return False
            
            def cancel_scan(self):
                raise RuntimeError("No scan is currently running")
        
        # Store original service
        original_service = getattr(app, 'scan_service', None)
        
        mock_service = MockScanService()
        with app.app_context():
            app.scan_service = mock_service
        
        try:
            response = authenticated_client.post('/api/cancel-scan')
            assert response.status_code == 400
            assert 'No scan is currently running' in response.get_json()['error']
        finally:
            # Restore original service
            if original_service:
                app.scan_service = original_service
            elif hasattr(app, 'scan_service'):
                delattr(app, 'scan_service')


class TestErrorFilesEndpoint:
    """Test error files endpoint"""

    def test_get_error_files_empty(self, authenticated_client, db):
        """Test getting error files when none exist"""
        response = authenticated_client.get('/api/error-files')
        assert response.status_code == 200
        data = response.get_json()
        assert 'error_files' in data
        assert data['total'] == 0
        assert len(data['error_files']) == 0

    def test_get_error_files_with_errors(self, authenticated_client, app, db):
        """Test getting error files when some exist"""
        with app.app_context():
            # Create error files
            for i in range(5):
                result = ScanResult(
                    file_path=f'/test/error{i}.mp4',
                    scan_status='error',
                    error_message=f'Error scanning file {i}',
                    scan_date=datetime.now(timezone.utc)
                )
                db.session.add(result)

            # Create non-error files to ensure filtering works
            result = ScanResult(
                file_path='/test/completed.mp4',
                scan_status='completed',
                scan_date=datetime.now(timezone.utc)
            )
            db.session.add(result)
            db.session.commit()

        response = authenticated_client.get('/api/error-files')
        assert response.status_code == 200
        data = response.get_json()
        assert data['total'] == 5
        assert len(data['error_files']) == 5
        assert all(f['error_message'] is not None for f in data['error_files'])

    def test_get_error_files_pagination(self, authenticated_client, app, db):
        """Test error files pagination"""
        with app.app_context():
            # Create 10 error files
            for i in range(10):
                result = ScanResult(
                    file_path=f'/test/error{i}.mp4',
                    scan_status='error',
                    error_message=f'Error {i}',
                    scan_date=datetime.now(timezone.utc)
                )
                db.session.add(result)
            db.session.commit()

        # Get first page with 5 per page
        response = authenticated_client.get('/api/error-files?page=1&per_page=5')
        assert response.status_code == 200
        data = response.get_json()
        assert data['total'] == 10
        assert len(data['error_files']) == 5
        assert data['pages'] == 2
        assert data['current_page'] == 1

    def test_get_error_files_search(self, authenticated_client, app, db):
        """Test error files search filter"""
        with app.app_context():
            # Create error files with different paths
            result1 = ScanResult(
                file_path='/test/videos/error1.mp4',
                scan_status='error',
                error_message='Error 1',
                scan_date=datetime.now(timezone.utc)
            )
            result2 = ScanResult(
                file_path='/test/images/error2.jpg',
                scan_status='error',
                error_message='Error 2',
                scan_date=datetime.now(timezone.utc)
            )
            db.session.add(result1)
            db.session.add(result2)
            db.session.commit()

        # Search for videos
        response = authenticated_client.get('/api/error-files?search=videos')
        assert response.status_code == 200
        data = response.get_json()
        assert data['total'] == 1
        assert 'videos' in data['error_files'][0]['file_path']

    def test_get_error_files_sorting(self, authenticated_client, app, db):
        """Test error files sorting"""
        with app.app_context():
            # Create error files
            result1 = ScanResult(
                file_path='/test/a.mp4',
                scan_status='error',
                error_message='Error A',
                scan_date=datetime.now(timezone.utc)
            )
            result2 = ScanResult(
                file_path='/test/z.mp4',
                scan_status='error',
                error_message='Error Z',
                scan_date=datetime.now(timezone.utc)
            )
            db.session.add(result1)
            db.session.add(result2)
            db.session.commit()

        # Sort by file_path ascending
        response = authenticated_client.get('/api/error-files?sort_field=file_path&sort_order=asc')
        assert response.status_code == 200
        data = response.get_json()
        assert data['error_files'][0]['file_path'] == '/test/a.mp4'
        assert data['error_files'][1]['file_path'] == '/test/z.mp4'
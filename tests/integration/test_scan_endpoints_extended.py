import pytest
from models import db, ScanResult, ScanState
from datetime import datetime, timezone

class TestScanManagementEndpoints:
    """Test scan management endpoints"""
    
    def test_reset_stuck_scans(self, client, app, db):
        """Test resetting stuck scans"""
        with app.app_context():
            # Create stuck scan results
            for i in range(3):
                result = ScanResult(
                    file_path=f'/test/stuck{i}.mp4',
                    scan_status='scanning',
                    scan_date=datetime.now(timezone.utc)
                )
                db.session.add(result)
            db.session.commit()
            
            # Reset stuck scans
            response = client.post('/api/reset-stuck-scans')
            assert response.status_code == 200
            data = response.get_json()
            assert data['count'] == 3
            assert 'Reset 3 stuck files' in data['message']
            
            # Verify reset
            stuck_count = ScanResult.query.filter_by(scan_status='scanning').count()
            assert stuck_count == 0
            pending_count = ScanResult.query.filter_by(scan_status='pending').count()
            assert pending_count == 3
    
    def test_reset_stuck_scans_none_found(self, client, db):
        """Test resetting when no stuck scans"""
        response = client.post('/api/reset-stuck-scans')
        assert response.status_code == 200
        data = response.get_json()
        assert data['count'] == 0
        assert 'Reset 0 stuck files' in data['message']
    
    def test_reset_for_rescan_single_file(self, client, app, db):
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
            response = client.post('/api/reset-files-by-path',
                json={'file_path': '/test/completed.mp4'})
            assert response.status_code == 200
            data = response.get_json()
            assert data['reset_count'] == 1
            
            # Verify reset
            db.session.refresh(result)
            assert result.scan_status == 'pending'
    
    def test_reset_for_rescan_multiple_files(self, client, app, db):
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
            response = client.post('/api/reset-files-by-path',
                json={'file_paths': files})
            assert response.status_code == 200
            data = response.get_json()
            assert data['reset_count'] == 3
    
    def test_reset_for_rescan_no_files(self, client, db):
        """Test resetting with no files specified"""
        response = client.post('/api/reset-files-by-path', json={})
        assert response.status_code == 400
        assert 'No file paths provided' in response.get_json()['error']
    
    def test_recover_stuck_scan(self, client, app, db):
        """Test recovering a stuck scan"""
        with app.app_context():
            # Create active scan state
            scan_state = ScanState.get_or_create()
            scan_state.is_active = True
            scan_state.phase = 'scanning'
            scan_state.start_time = datetime.now(timezone.utc)
            db.session.commit()
            
            # Recover scan
            response = client.post('/api/recover-stuck-scan')
            assert response.status_code == 200
            data = response.get_json()
            assert 'Scan state recovered' in data['message']
            assert data['stuck_files_reset'] >= 0
            
            # Verify recovery
            db.session.refresh(scan_state)
            assert scan_state.is_active is False
            assert scan_state.phase == 'error'
    
    def test_recover_stuck_scan_not_stuck(self, client, app, db):
        """Test recovering when scan not stuck"""
        with app.app_context():
            # Create idle scan state
            scan_state = ScanState.get_or_create()
            scan_state.is_active = False
            scan_state.phase = 'idle'
            db.session.commit()
            
            # Try to recover
            response = client.post('/api/recover-stuck-scan')
            assert response.status_code == 200
            # The endpoint always returns success even if no scan was stuck


class TestScanCancellationEndpoint:
    """Test scan cancellation endpoint"""
    
    def test_cancel_scan_success(self, client, app, db, monkeypatch):
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
            response = client.post('/api/cancel-scan')
            assert response.status_code == 200
            assert 'Scan cancelled successfully' in response.get_json()['message']
        finally:
            # Restore original service
            if original_service:
                app.scan_service = original_service
            elif hasattr(app, 'scan_service'):
                delattr(app, 'scan_service')
    
    def test_cancel_scan_not_running(self, client, app, db, monkeypatch):
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
            response = client.post('/api/cancel-scan')
            assert response.status_code == 400
            assert 'No scan is currently running' in response.get_json()['error']
        finally:
            # Restore original service
            if original_service:
                app.scan_service = original_service
            elif hasattr(app, 'scan_service'):
                delattr(app, 'scan_service')
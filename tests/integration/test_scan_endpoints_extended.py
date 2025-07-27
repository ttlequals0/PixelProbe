import pytest
from models import db, ScanResult, ScanState
from datetime import datetime, timezone

class TestScanManagementEndpoints:
    """Test scan management endpoints"""
    
    def test_reset_stuck_scans(self, client, app):
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
            assert data['reset_count'] == 3
            assert data['message'] == '3 stuck scans have been reset to pending'
            
            # Verify reset
            stuck_count = ScanResult.query.filter_by(scan_status='scanning').count()
            assert stuck_count == 0
            pending_count = ScanResult.query.filter_by(scan_status='pending').count()
            assert pending_count == 3
    
    def test_reset_stuck_scans_none_found(self, client):
        """Test resetting when no stuck scans"""
        response = client.post('/api/reset-stuck-scans')
        assert response.status_code == 200
        data = response.get_json()
        assert data['reset_count'] == 0
        assert 'No stuck scans found' in data['message']
    
    def test_reset_for_rescan_single_file(self, client, app):
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
            response = client.post('/api/reset-for-rescan',
                json={'file_path': '/test/completed.mp4'})
            assert response.status_code == 200
            data = response.get_json()
            assert data['reset_count'] == 1
            
            # Verify reset
            db.session.refresh(result)
            assert result.scan_status == 'pending'
    
    def test_reset_for_rescan_multiple_files(self, client, app):
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
            response = client.post('/api/reset-for-rescan',
                json={'file_paths': files})
            assert response.status_code == 200
            data = response.get_json()
            assert data['reset_count'] == 3
    
    def test_reset_for_rescan_no_files(self, client):
        """Test resetting with no files specified"""
        response = client.post('/api/reset-for-rescan', json={})
        assert response.status_code == 400
        assert 'No file paths provided' in response.get_json()['error']
    
    def test_recover_stuck_scan(self, client, app):
        """Test recovering a stuck scan"""
        with app.app_context():
            # Create active scan state
            scan_state = ScanState.get_or_create()
            scan_state.is_scanning = True
            scan_state.scan_phase = 'scanning'
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
            assert scan_state.is_scanning is False
            assert scan_state.scan_phase == 'idle'
    
    def test_recover_stuck_scan_not_stuck(self, client, app):
        """Test recovering when scan not stuck"""
        with app.app_context():
            # Create idle scan state
            scan_state = ScanState.get_or_create()
            scan_state.is_scanning = False
            scan_state.scan_phase = 'idle'
            db.session.commit()
            
            # Try to recover
            response = client.post('/api/recover-stuck-scan')
            assert response.status_code == 400
            assert 'No stuck scan found' in response.get_json()['error']


class TestScanCancellationEndpoint:
    """Test scan cancellation endpoint"""
    
    def test_cancel_scan_success(self, client, app, monkeypatch):
        """Test successful scan cancellation"""
        # Mock scan service
        class MockScanService:
            def is_scan_running(self):
                return True
            
            def cancel_scan(self):
                return {'message': 'Scan cancelled successfully'}
        
        mock_service = MockScanService()
        monkeypatch.setattr('pixelprobe.api.scan_routes.scan_service', mock_service)
        
        response = client.post('/api/cancel-scan')
        assert response.status_code == 200
        assert 'Scan cancelled successfully' in response.get_json()['message']
    
    def test_cancel_scan_not_running(self, client, monkeypatch):
        """Test cancelling when no scan running"""
        # Mock scan service
        class MockScanService:
            def is_scan_running(self):
                return False
            
            def cancel_scan(self):
                raise RuntimeError("No scan is currently running")
        
        mock_service = MockScanService()
        monkeypatch.setattr('pixelprobe.api.scan_routes.scan_service', mock_service)
        
        response = client.post('/api/cancel-scan')
        assert response.status_code == 400
        assert 'No scan is currently running' in response.get_json()['error']
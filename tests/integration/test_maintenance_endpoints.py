import pytest
from models import db, CleanupState, FileChangesState
import time

class TestMaintenanceCancelEndpoints:
    """Test maintenance cancellation endpoints"""
    
    def test_cancel_cleanup(self, client, app):
        """Test cancelling cleanup operation"""
        with app.app_context():
            # Create active cleanup state
            cleanup = CleanupState(
                is_active=True,
                phase='checking_files',
                phase_number=2,
                cancel_requested=False
            )
            db.session.add(cleanup)
            db.session.commit()
            
            # Cancel cleanup
            response = client.post('/api/cancel-cleanup')
            assert response.status_code == 200
            assert 'cancellation requested' in response.get_json()['message']
            
            # Verify cancel flag set
            db.session.refresh(cleanup)
            assert cleanup.cancel_requested is True
            assert cleanup.progress_message == 'Cancellation requested...'
    
    def test_cancel_cleanup_no_active(self, client):
        """Test cancelling when no active cleanup"""
        response = client.post('/api/cancel-cleanup')
        assert response.status_code == 400
        assert 'No active cleanup' in response.get_json()['error']
    
    def test_cancel_file_changes(self, client, app):
        """Test cancelling file changes check"""
        with app.app_context():
            # Create active file changes state
            check = FileChangesState(
                check_id='test-check-123',
                is_active=True,
                phase='checking_hashes',
                phase_number=2,
                cancel_requested=False
            )
            db.session.add(check)
            db.session.commit()
            
            # Cancel file changes
            response = client.post('/api/cancel-file-changes')
            assert response.status_code == 200
            assert 'cancellation requested' in response.get_json()['message']
            
            # Verify cancel flag set
            db.session.refresh(check)
            assert check.cancel_requested is True
            assert check.progress_message == 'Cancellation requested...'
    
    def test_reset_cleanup_state(self, client, app):
        """Test resetting cleanup state"""
        with app.app_context():
            # Create stuck cleanup state
            cleanup = CleanupState(
                is_active=True,
                phase='error',
                phase_number=2
            )
            db.session.add(cleanup)
            db.session.commit()
            
            # Reset state
            response = client.post('/api/reset-cleanup-state')
            assert response.status_code == 200
            
            # Verify reset
            db.session.refresh(cleanup)
            assert cleanup.is_active is False
            assert cleanup.phase == 'reset'
    
    def test_reset_file_changes_state(self, client, app):
        """Test resetting file changes state"""
        with app.app_context():
            # Create stuck file changes state
            check = FileChangesState(
                check_id='test-123',
                is_active=True,
                phase='error'
            )
            db.session.add(check)
            db.session.commit()
            
            # Reset state
            response = client.post('/api/reset-file-changes-state')
            assert response.status_code == 200
            
            # Verify reset
            db.session.refresh(check)
            assert check.is_active is False
            assert check.phase == 'reset'


class TestMaintenanceOperationEndpoints:
    """Test maintenance operation endpoints"""
    
    def test_start_cleanup_orphaned(self, client, app, monkeypatch):
        """Test starting orphaned cleanup"""
        # Mock the async function to prevent actual execution
        def mock_cleanup(*args):
            pass
        monkeypatch.setattr('pixelprobe.api.maintenance_routes.cleanup_orphaned_async', mock_cleanup)
        
        response = client.post('/api/cleanup-orphaned')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'started'
        assert 'cleanup_id' in data
        
        # Verify cleanup state created
        with app.app_context():
            cleanup = CleanupState.query.filter_by(is_active=True).first()
            assert cleanup is not None
            assert cleanup.phase == 'starting'
    
    def test_cleanup_already_running(self, client, app, monkeypatch):
        """Test starting cleanup when already running"""
        # Mock thread check
        import threading
        mock_thread = threading.Thread(target=lambda: None)
        mock_thread.start()
        monkeypatch.setattr('pixelprobe.api.maintenance_routes.current_cleanup_thread', mock_thread)
        
        response = client.post('/api/cleanup-orphaned')
        assert response.status_code == 409
        assert 'already in progress' in response.get_json()['error']
        
        mock_thread.join()
    
    def test_vacuum_database(self, client, app):
        """Test vacuum database endpoint"""
        response = client.post('/api/vacuum')
        assert response.status_code == 200
        data = response.get_json()
        assert 'message' in data
        assert 'before_size' in data
        assert 'after_size' in data
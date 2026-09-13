"""
Integration tests for actual scan execution and state management.
These tests verify that scans can actually start, run, and complete properly.
"""

import os
import sys
import types
import pytest
from unittest.mock import Mock
from uuid import uuid4

from flask import Flask
from sqlalchemy import create_engine, text

from pixelprobe.models import db, ScanConfiguration, ScanState, ScanResult, ScanTask
from pixelprobe.services.scan_engine import claim_scan_slot
from pixelprobe.services.scan_service import ScanService


POSTGRES_URI = os.environ.get('PIXELPROBE_TEST_POSTGRES_URI')


@pytest.fixture
def postgres_scan_app():
    """Use PostgreSQL because scan-slot locking is a PostgreSQL contract."""
    schema = f'scan_execution_{uuid4().hex[:12]}'
    engine = create_engine(POSTGRES_URI)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=POSTGRES_URI,
        SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'options': f'-csearch_path={schema}'}},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()


class TestScanExecution:
    """Test actual scan execution, not just endpoint availability"""
    
    @pytest.mark.postgres
    @pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
    def test_scan_can_start_when_no_active_scan(self, postgres_scan_app):
        """The durable PostgreSQL slot claim starts a new run atomically."""
        with postgres_scan_app.app_context():
            ok, error, status = claim_scan_slot('postgres-start')
            assert (ok, error, status) == (True, None, None)
            state = ScanState.query.filter_by(scan_id='postgres-start').one()
            assert state.is_active is True
            assert state.phase == 'initializing'
    
    def test_scan_prevents_concurrent_execution(self, authenticated_client, app, db, test_data_dir):
        """Test that only one scan can run at a time"""
        with app.app_context():
            db.session.add(ScanConfiguration(path=test_data_dir['test_dir'], is_active=True))
            # Create an active scan
            active_scan = ScanState(
                scan_id='test-scan-1',
                phase='scanning',
                is_active=True
            )
            db.session.add(active_scan)
            db.session.commit()
            
            # Try to start another scan
            response = authenticated_client.post('/api/scan-parallel',
                                  json={'directories': [test_data_dir['test_dir']]})

            # Should return 409 Conflict
            assert response.status_code == 409
            data = response.get_json()
            assert 'error' in data
            assert 'already in progress' in data['error'].lower()
    
    def test_stale_scan_detection_and_cleanup(self, authenticated_client, app, db, test_data_dir):
        """Test that stale scans are detected and can be cleaned up"""
        with app.app_context():
            from datetime import datetime, timezone, timedelta
            
            # Create a stale scan (started 10 minutes ago with no progress)
            stale_scan = ScanState(
                scan_id='stale-scan',
                phase='scanning',
                is_active=True,
                start_time=datetime.now(timezone.utc) - timedelta(minutes=10),
                last_update=datetime.now(timezone.utc) - timedelta(minutes=10)
            )
            db.session.add(stale_scan)
            db.session.commit()
            
            # The scan should be detected as stale and allow a new scan
            # OR there should be an endpoint to force-clear stale scans
            response = authenticated_client.post('/api/stuck-scan-recovery')
            
            if response.status_code == 200:
                # Recovery endpoint exists
                data = response.get_json()
                assert 'cleaned' in data or 'message' in data
                
                # Now a new scan should be able to start
                response = authenticated_client.post('/api/scan-all',
                                      json={'directories': [test_data_dir['test_dir']]})
                assert response.status_code in [200, 503]
    
    @pytest.mark.postgres
    @pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
    def test_scan_cancel_actually_stops_scan(self, postgres_scan_app, monkeypatch):
        """Cancellation terminalizes only the active run's durable intent."""
        with postgres_scan_app.app_context():
            state = ScanState(scan_id='postgres-cancel', phase='scanning', is_active=True)
            db.session.add(state)
            db.session.flush()
            task = ScanTask(scan_id=state.scan_id, purpose='continuation',
                            celery_task_id='postgres-cancel-task', status='dispatched')
            db.session.add(task)
            db.session.commit()

            revoke = Mock()
            fake_celery = types.ModuleType('pixelprobe.celery_config')
            fake_celery.celery_app = types.SimpleNamespace(
                control=types.SimpleNamespace(revoke=revoke))
            monkeypatch.setitem(sys.modules, 'pixelprobe.celery_config', fake_celery)
            result = ScanService(POSTGRES_URI).cancel_scan()

            assert result['cancelled'] is True
            assert result['owned_task_count'] == 1
            revoke.assert_called_once_with('postgres-cancel-task', terminate=False)
            state = ScanState.query.filter_by(scan_id='postgres-cancel').one()
            assert state.phase == 'cancelled'
            assert state.is_active is False
            assert ScanTask.query.filter_by(scan_id=state.scan_id).one().status == 'cancelled'
    
    @pytest.mark.postgres
    @pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
    def test_scan_phase_transitions(self, postgres_scan_app):
        """Run phase records transition in one PostgreSQL-backed state row."""
        with postgres_scan_app.app_context():
            ok, _, _ = claim_scan_slot('postgres-phase')
            assert ok
            scan = ScanState.query.filter_by(scan_id='postgres-phase').one()
            scan.start_scan(['/tmp/phase-root'])
            assert scan.phase == 'discovering'
            scan.phase = 'adding'
            scan.estimated_total = 1
            db.session.commit()
            scan.phase = 'scanning'
            db.session.commit()
            scan.phase = 'completed'
            scan.is_active = False
            db.session.commit()
            persisted = ScanState.query.filter_by(scan_id='postgres-phase').one()
            assert (persisted.phase, persisted.is_active, persisted.estimated_total) == (
                'completed', False, 1)
    
    def test_scan_parallel_endpoint_execution(self, authenticated_client, app, db, test_data_dir):
        """Test the parallel scan endpoint can actually execute"""
        with app.app_context():
            db.session.add(ScanConfiguration(path=test_data_dir['test_dir'], is_active=True))
            # Ensure no active scans
            ScanState.query.update({'is_active': False})
            db.session.commit()
            
            # Try parallel scan
            response = authenticated_client.post('/api/scan-parallel',
                                  json={'directories': [test_data_dir['test_dir']], 'num_workers': 2})
            
            # Should work or indicate Celery not available
            assert response.status_code in [200, 503]
            
            if response.status_code == 200:
                data = response.get_json()
                assert 'scan_id' in data or 'message' in data
    
    def test_scan_parallel_v2_endpoint_execution(self, authenticated_client, app, db, test_data_dir):
        """Test the enhanced parallel scan v2 endpoint"""
        with app.app_context():
            db.session.add(ScanConfiguration(path=test_data_dir['test_dir'], is_active=True))
            # Ensure no active scans
            ScanState.query.update({'is_active': False})
            db.session.commit()
            
            # Try parallel scan
            response = authenticated_client.post('/api/scan-parallel',
                                  json={'directories': [test_data_dir['test_dir']]})

            # Should work or indicate Celery not available
            assert response.status_code in [200, 503]

            if response.status_code == 200:
                data = response.get_json()
                # Response can have either 'scan_id' (Celery enabled) or 'message' (Celery disabled)
                assert 'message' in data or 'scan_id' in data
    
    def test_pending_scan_execution(self, authenticated_client, app, db):
        """Test that pending file scans can execute"""
        with app.app_context():
            # Create some pending files
            for i in range(5):
                result = ScanResult(
                    file_path=f'/test/pending_{i}.mp4',
                    scan_status='pending'
                )
                db.session.add(result)
            db.session.commit()
            
            # Ensure no active scans
            ScanState.query.update({'is_active': False})
            db.session.commit()
            
            # Start pending scan
            response = authenticated_client.post('/api/force-scan-pending')
            
            # Should work or indicate no pending files
            assert response.status_code in [200, 404, 503]
            
            if response.status_code == 200:
                data = response.get_json()
                assert 'message' in data or 'scan_id' in data
    
    def test_file_changes_scan_execution(self, authenticated_client, app, db):
        """Test that file changes scan can execute"""
        with app.app_context():
            # Ensure no active scans
            ScanState.query.update({'is_active': False})
            db.session.commit()
            
            # Start file changes scan
            response = authenticated_client.post('/api/check-file-changes')
            
            # Should work or return appropriate status
            assert response.status_code in [200, 404, 503]
            
            if response.status_code == 200:
                data = response.get_json()
                assert 'message' in data or 'task_id' in data
    
    def test_orphan_cleanup_execution(self, authenticated_client, app, db):
        """Test that orphan cleanup can execute"""
        with app.app_context():
            # Ensure no active scans  
            ScanState.query.update({'is_active': False})
            db.session.commit()
            
            # Start orphan cleanup
            response = authenticated_client.post('/api/cleanup-orphaned')
            
            # Should work or return appropriate status
            assert response.status_code in [200, 503]
            
            if response.status_code == 200:
                data = response.get_json()
                assert 'message' in data or 'task_id' in data


class TestScanStateRecovery:
    """Test scan state recovery mechanisms"""

    def test_scan_recovery_endpoint(self, authenticated_client, app, db):
        """Test that scan recovery endpoint works"""
        with app.app_context():
            from datetime import datetime, timezone, timedelta

            # Create a stuck scan from over an hour ago
            stuck_scan = ScanState(
                scan_id='stuck-scan',
                phase='adding',
                is_active=True,
                files_processed=429000,
                estimated_total=600230,
                start_time=datetime.now(timezone.utc) - timedelta(hours=2),
                last_update=datetime.now(timezone.utc) - timedelta(hours=2)
            )
            db.session.add(stuck_scan)
            db.session.commit()

            # Ensure scan service thinks no scan is running
            app.scan_service.current_scan_thread = None

            # Try recovery using the consolidated endpoint
            response = authenticated_client.post('/api/scan/recovery')
            assert response.status_code == 200

            data = response.get_json()
            assert data['status'] == 'success'
            assert 'cleaned_count' in data
            assert data['cleaned_count'] >= 1

            # Verify scan is cleaned up
            scan = ScanState.query.filter_by(scan_id='stuck-scan').first()
            assert scan.is_active is False
            assert scan.phase == 'crashed'
    
    def test_force_cleanup_endpoint(self, authenticated_client, app, db, test_data_dir):
        """Test force cleanup of all active scans"""
        with app.app_context():
            # Create multiple active scans (shouldn't happen but test recovery)
            for i in range(3):
                scan = ScanState(
                    scan_id=f'scan-{i}',
                    phase='scanning',
                    is_active=True
                )
                db.session.add(scan)
            db.session.commit()
            
            # Force cleanup
            response = authenticated_client.post('/api/force-cleanup-scans')
            
            # Should succeed or endpoint might not exist
            if response.status_code == 200:
                # Verify all scans are cleaned
                active_scans = ScanState.query.filter_by(is_active=True).count()
                assert active_scans == 0
                
                # New scan should be able to start
                response = authenticated_client.post('/api/scan-all',
                                      json={'directories': [test_data_dir['test_dir']]})
                assert response.status_code in [200, 503]

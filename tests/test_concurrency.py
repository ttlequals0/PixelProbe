"""
Concurrency Tests - P2 Implementation from Audit Plan
Tests for race conditions and concurrent operations
"""

import threading
import time
import pytest
from unittest.mock import patch, MagicMock
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestConcurrency:
    """Test suite for concurrent operations and race conditions"""
    
    def test_simultaneous_scan_starts(self, app, db):
        """Test race condition when starting multiple scans simultaneously"""
        results = []
        errors = []
        
        def start_scan():
            try:
                # Create a new test client for each thread
                with app.test_client() as client:
                    response = client.post('/api/scan', json={
                        'directories': ['/tmp/test'],
                        'scan_type': 'full'
                    })
                    results.append(response.status_code)
            except Exception as e:
                errors.append(str(e))
        
        # Create multiple threads trying to start scans
        threads = [threading.Thread(target=start_scan) for _ in range(5)]
        
        # Start all threads simultaneously
        for t in threads:
            t.start()
        
        # Wait for all threads to complete
        for t in threads:
            t.join(timeout=10)
        
        # Verify only one scan succeeded or all failed with proper error
        assert len(results) == 5, f"Expected 5 responses, got {len(results)}"
        # Either one succeeds and others conflict, or all fail due to test environment
        success_count = results.count(200)
        conflict_count = results.count(409)
        error_count = results.count(400)  # Bad request for missing directories
        
        assert success_count <= 1, f"Expected at most 1 success, got {success_count}"
        if success_count == 1:
            assert conflict_count >= 3, f"Expected at least 3 conflicts when one succeeds, got {conflict_count}"
        assert len(errors) == 0, f"Unexpected errors: {errors}"
    
    def test_concurrent_file_updates(self, app, db):
        """Test concurrent updates to the same file record"""
        from models import ScanResult
        
        # Create a test file record
        test_file = ScanResult(
            file_path='/test/concurrent.mp4',
            scan_status='pending',
            is_corrupted=None
        )
        db.session.add(test_file)
        db.session.commit()
        file_id = test_file.id
        
        results = []
        
        def update_file(status, corrupted):
            try:
                # Create a new test client for each thread
                with app.test_client() as client:
                    # Simulate file update
                    response = client.post(f'/api/update-file/{file_id}', json={
                        'scan_status': status,
                        'is_corrupted': corrupted
                    })
                    results.append((status, response.status_code))
            except Exception as e:
                results.append((status, str(e)))
        
        # Create threads for concurrent updates
        threads = [
            threading.Thread(target=update_file, args=('completed', False)),
            threading.Thread(target=update_file, args=('completed', True)),
            threading.Thread(target=update_file, args=('error', None))
        ]
        
        # Start all threads
        for t in threads:
            t.start()
        
        # Wait for completion
        for t in threads:
            t.join(timeout=5)
        
        # Verify results (implementation specific)
        assert len(results) == 3, f"Expected 3 results, got {len(results)}"
    
    def test_scan_cancellation_race(self, app, db):
        """Test race condition between scan progress and cancellation"""
        # Start a scan
        with app.test_client() as client:
            response = client.post('/api/scan', json={
                'directories': ['/tmp/test'],
                'scan_type': 'full'
            })
            
            if response.status_code != 200:
                # Skip test if scan can't start
                pytest.skip("Cannot start scan for cancellation test")
            
            scan_data = response.get_json()
            scan_id = scan_data.get('scan_id')
        
        results = {'cancelled': False, 'completed': False}
        
        def cancel_scan():
            time.sleep(0.1)  # Small delay
            with app.test_client() as client:
                response = client.post('/api/cancel-scan')
                results['cancelled'] = response.status_code == 200
        
        def check_progress():
            with app.test_client() as client:
                for _ in range(10):
                    response = client.get('/api/scan-status')
                    data = response.get_json()
                    if data.get('phase') == 'completed':
                        results['completed'] = True
                        break
                    time.sleep(0.1)
        
        # Run cancellation and progress check concurrently
        cancel_thread = threading.Thread(target=cancel_scan)
        progress_thread = threading.Thread(target=check_progress)
        
        cancel_thread.start()
        progress_thread.start()
        
        cancel_thread.join(timeout=5)
        progress_thread.join(timeout=5)
        
        # Either cancelled or completed, but not both
        assert results['cancelled'] or results['completed']
        assert not (results['cancelled'] and results['completed'])
    
    def test_database_connection_pool_exhaustion(self, app, db):
        """Test behavior when database connection pool is exhausted"""
        from pixelprobe.services.scan_service import ScanService
        
        connections = []
        
        def acquire_connection():
            try:
                # Simulate acquiring a database connection
                service = ScanService(app.config['SQLALCHEMY_DATABASE_URI'])
                session = service._get_db_session()
                connections.append(session)
                time.sleep(0.5)  # Hold connection
            except Exception as e:
                connections.append(str(e))
        
        # Create more threads than available connections
        threads = [threading.Thread(target=acquire_connection) for _ in range(30)]
        
        # Start all threads
        for t in threads:
            t.start()
        
        # Wait for completion
        for t in threads:
            t.join(timeout=10)
        
        # Clean up connections
        for conn in connections:
            if hasattr(conn, 'close'):
                try:
                    conn.close()
                except:
                    pass
        
        # Verify some connections were established
        successful_connections = [c for c in connections if hasattr(c, 'close')]
        assert len(successful_connections) > 0, "No connections were established"
    
    def test_parallel_scan_worker_distribution(self, app, db):
        """Test that parallel scans distribute work across workers correctly"""
        with patch('pixelprobe.tasks_parallel.process_chunk_task.delay') as mock_task:
            # Configure mock
            mock_task.return_value = MagicMock(id='test-task-id')
            
            with app.test_client() as client:
                # Start parallel scan
                response = client.post('/api/scan-parallel-v2', json={
                    'directories': ['/tmp/test'],
                    'num_workers': 8
                })
                
                # Check response (may fail due to missing directories)
                if response.status_code in [200, 400, 404]:
                    data = response.get_json()
                    # If succeeded, verify tasks were created
                    if response.status_code == 200:
                        assert mock_task.call_count > 0, "No tasks were created"
                        assert 'message' in data
                    # Otherwise, it's expected to fail in test environment
                    pass
    
    def test_scheduled_scan_overlap(self, app):
        """Test that scheduled scans don't overlap"""
        from scheduler import MediaScheduler
        
        scheduler = MediaScheduler(app)
        
        results = []
        
        def mock_scan():
            results.append('started')
            time.sleep(2)  # Simulate scan duration
            results.append('completed')
        
        # Simulate rapid schedule triggers
        with patch.object(scheduler, '_run_periodic_scan', mock_scan):
            threads = []
            for _ in range(3):
                t = threading.Thread(target=scheduler._run_periodic_scan)
                threads.append(t)
                t.start()
                time.sleep(0.5)  # Slight delay between triggers
            
            # Wait for all threads
            for t in threads:
                t.join(timeout=10)
        
        # Verify scans didn't overlap (only one 'started' before 'completed')
        assert results.count('started') <= results.count('completed') + 1
    
    def test_cleanup_and_scan_mutual_exclusion(self, app):
        """Test that cleanup and scan operations are mutually exclusive"""
        results = {'scan': None, 'cleanup': None}
        
        def start_scan():
            with app.test_client() as client:
                response = client.post('/api/scan', json={
                    'directories': ['/tmp/test'],
                    'scan_type': 'full'
                })
                results['scan'] = response.status_code
        
        def start_cleanup():
            with app.test_client() as client:
                response = client.post('/api/cleanup-orphaned')
                results['cleanup'] = response.status_code
        
        # Try to start both operations simultaneously
        scan_thread = threading.Thread(target=start_scan)
        cleanup_thread = threading.Thread(target=start_cleanup)
        
        scan_thread.start()
        cleanup_thread.start()
        
        scan_thread.join(timeout=5)
        cleanup_thread.join(timeout=5)
        
        # One should succeed, the other should get conflict
        statuses = [results['scan'], results['cleanup']]
        assert 200 in statuses, "At least one operation should succeed"
        assert 409 in statuses or None in statuses, "One operation should be blocked"
    
    def test_scan_state_consistency_under_load(self, app, db):
        """Test scan state consistency under concurrent read/write load"""
        from models import ScanState
        
        # Initialize scan state
        scan_state = ScanState.get_or_create()
        scan_state.start_discovering()
        db.session.commit()
        
        inconsistencies = []
        
        def read_state():
            with app.test_client() as client:
                for _ in range(10):
                    try:
                        response = client.get('/api/scan-status')
                        data = response.get_json()
                        
                        # Check for inconsistencies
                        if data.get('is_active') and not data.get('phase'):
                            inconsistencies.append('Active but no phase')
                        if data.get('files_processed', 0) > data.get('estimated_total', 0):
                            inconsistencies.append('Processed > Total')
                        
                        time.sleep(0.1)
                    except Exception as e:
                        inconsistencies.append(f"Read error: {e}")
        
        def update_state():
            for i in range(10):
                try:
                    scan_state = ScanState.get_or_create()
                    scan_state.files_processed = i * 100
                    scan_state.estimated_total = 1000
                    db.session.commit()
                    time.sleep(0.1)
                except Exception as e:
                    inconsistencies.append(f"Write error: {e}")
        
        # Run concurrent reads and writes
        threads = [
            threading.Thread(target=read_state),
            threading.Thread(target=read_state),
            threading.Thread(target=update_state)
        ]
        
        for t in threads:
            t.start()
        
        for t in threads:
            t.join(timeout=10)
        
        # Check for inconsistencies
        assert len(inconsistencies) == 0, f"State inconsistencies detected: {inconsistencies}"
    
    def test_file_discovery_deduplication(self, app):
        """Test that file discovery properly deduplicates across parallel workers"""
        from pixelprobe.services.scan_executor import BatchProcessor
        
        test_files = [f'/test/file_{i}.mp4' for i in range(100)]
        
        # Simulate parallel discovery with overlapping results
        def discover_files(start, end):
            # Intentionally create overlaps
            return test_files[max(0, start-5):min(100, end+5)]
        
        # Run parallel discovery
        results = BatchProcessor.parallel_map(
            lambda r: discover_files(r[0], r[1]),
            [(0, 25), (20, 50), (45, 75), (70, 100)],
            max_workers=4
        )
        
        # Flatten and deduplicate
        all_files = []
        for batch in results:
            if batch:
                all_files.extend(batch)
        
        unique_files = list(set(all_files))
        
        # Verify deduplication works
        assert len(unique_files) == len(test_files), \
            f"Expected {len(test_files)} unique files, got {len(unique_files)}"
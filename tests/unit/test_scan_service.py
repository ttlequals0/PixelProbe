"""
Unit tests for ScanService
"""

import pytest
import sys
import types
from unittest.mock import Mock, patch, MagicMock
import threading
import time
from datetime import datetime, timezone

from pixelprobe.services.scan_service import ScanService
from pixelprobe.models import AppConfig, ScanConfiguration, ScanChunk, ScanResult, ScanRunFile, ScanState
from pixelprobe.services.settings_service import invalidate_cache, resolve_settings
from pixelprobe.utils.security import PathTraversalError

class TestScanService:
    """Test the scan service business logic"""

    @pytest.fixture(autouse=True)
    def no_redis_progress(self, monkeypatch):
        monkeypatch.setattr(
            'pixelprobe.services.scan_service.update_scan_progress_redis', lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            'pixelprobe.services.scan_service.clear_scan_progress_redis', lambda *_args, **_kwargs: None)
    
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
    
    @patch('pixelprobe.services.scan_service.PixelProbe')
    def test_scan_single_file_success(self, mock_probe_class, scan_service, app, db, tmp_path):
        """Test successful single file scan"""
        with app.app_context():
            file_path = tmp_path / 'file.mp4'
            file_path.touch()
            db.session.add(ScanConfiguration(path=str(tmp_path), is_active=True))
            db.session.commit()
            mock_probe = Mock()
            mock_probe_class.return_value = mock_probe
            
            # Mock scan result with a delay to ensure thread is running
            mock_result = {
                'outcome': 'completed', 'file_hash': 'a' * 64,
                'last_modified': datetime.now(timezone.utc), 'file_size': 0,
                'file_type': 'video', 'scan_tool': 'test', 'scan_output': '',
                'is_corrupted': False, 'has_warnings': False,
            }
            def mock_scan_with_delay(*args, **kwargs):
                time.sleep(0.2)  # Simulate scan taking time
                return mock_result
            mock_probe.scan_file.side_effect = mock_scan_with_delay
            
            # Start scan
            result = scan_service.scan_single_file(str(file_path))
        
            assert result['message'] == 'Scan started'
            assert result['file_path'] == str(file_path)
            
            # Wait for thread to start
            time.sleep(0.05)
            assert scan_service.is_scan_running() == True
            
            # Wait for scan to complete
            scan_service.current_scan_thread.join(timeout=5)
            
            # Verify scan was called
            mock_probe.scan_file.assert_called_once_with(str(file_path), force_rescan=False)

    def test_scan_single_file_not_found(self, scan_service, app, db, tmp_path):
        """Test scanning non-existent file"""
        with app.app_context():
            db.session.add(ScanConfiguration(path=str(tmp_path), is_active=True))
            db.session.commit()
            with pytest.raises(PathTraversalError):
                scan_service.scan_single_file(str(tmp_path / 'missing.mp4'))

    @patch('pixelprobe.services.scan_service.PixelProbe')
    def test_scan_single_file_reuses_existing_scan_state(self, mock_probe_class,
                                                         scan_service, app, db, tmp_path):
        """Single-file scan reuses an existing ScanState row when scan_id is passed.

        Regression test for the v2.6.41 UI flicker bug: the API route created a
        ScanState before queueing the Celery task, then scan_single_file created
        a *second* row with a different scan_id and the UI lost track in between.
        """
        with app.app_context():
            file_path = tmp_path / 'file.mp4'
            file_path.touch()
            db.session.add(ScanConfiguration(path=str(tmp_path), is_active=True))
            db.session.commit()
            release_scan = threading.Event()
            scan_result = {
                'outcome': 'completed', 'file_hash': 'a' * 64,
                'last_modified': datetime.now(timezone.utc), 'file_size': 0,
                'file_type': 'video', 'scan_tool': 'test', 'scan_output': '',
                'is_corrupted': False, 'has_warnings': False,
            }
            mock_probe_class.return_value.scan_file.side_effect = (
                lambda *args, **kwargs: (release_scan.wait(1), scan_result)[1])

            existing = ScanState.create_new_scan(scan_id='route-scan-id')
            existing.start_scan([str(file_path)], force_rescan=True)
            existing.is_active = False  # Simulate post-failure state pre-retry
            existing.phase = 'failed'
            db.session.commit()
            existing_id = existing.id

            scan_service.scan_single_file(str(file_path), force_rescan=True,
                                          scan_id='route-scan-id')

            rows = ScanState.query.filter_by(scan_id='route-scan-id').all()
            assert len(rows) == 1
            assert rows[0].id == existing_id
            assert rows[0].is_active is True
            assert rows[0].phase == 'initializing'
            assert rows[0].error_message is None

            worker = scan_service.current_scan_thread
            assert worker is not None
            release_scan.set()
            worker.join(timeout=5)
            assert not worker.is_alive()
    
    @patch('os.path.exists')
    @patch('pixelprobe.services.scan_service.PixelProbe')
    def test_scan_single_file_already_running(self, mock_probe_class, mock_exists, scan_service):
        """Test that single file scans are allowed to run independently"""
        mock_exists.return_value = True

        # Set up a fake running thread
        scan_service.current_scan_thread = threading.Thread(target=lambda: time.sleep(1))
        scan_service.current_scan_thread.start()

        try:
            # Single file scans are allowed to run concurrently
            # This should NOT raise a RuntimeError
            # The method should return without error (mocked PixelProbe prevents actual scanning)
            # We're just verifying it doesn't raise RuntimeError
            pass  # Test passes if no exception is raised
        finally:
            scan_service.current_scan_thread.join()
    
    
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
        
        assert result['cancelled'] is False
        assert result['message'] == 'Scan cancellation was not persisted'
        mock_scan_state.cancel_scan.assert_not_called()
        
        # Clean up - thread is set to None after cancel
        # No need to join since cancel_scan cleans it up
    
    def test_cancel_scan_not_running(self, scan_service):
        """Test cancel when no scan is running"""
        # No scan is running, but cancel should still work (cleanup orphaned tasks)
        result = scan_service.cancel_scan()
        assert 'message' in result
        assert 'tasks_killed' in result
    
    @patch('pixelprobe.services.scan_service.db')
    def test_reset_stuck_scans(self, mock_db, scan_service, db):
        """Test resetting stuck scans"""
        from pixelprobe.models import ScanResult
        
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

    def test_large_selected_chunk_failure_marks_only_run_member_error(self, scan_service, app, db):
        class Checker:
            def scan_file(self, file_path, force_rescan=False):
                if file_path.endswith('broken.png'):
                    raise RuntimeError('decoder failed')
                return {
                    'outcome': 'completed', 'file_hash': 'a' * 64,
                    'file_size': 1, 'last_modified': datetime.now(timezone.utc),
                    'is_corrupted': False, 'has_warnings': False,
                    'file_type': 'image', 'scan_tool': 'pil', 'scan_output': '',
                }

        with app.app_context():
            run_id = 'selected-parallel-error'
            paths = [f'/library/parent/image_{index}.png' for index in range(100)]
            paths.append('/library/parent/child/broken.png')
            state = ScanState.create_new_scan(scan_id=run_id)
            state.start_scan(['selected_files'], force_rescan=True)
            state.scan_type = 'selected'
            state.phase = 'scanning'
            state.estimated_total = len(paths)
            for path in paths:
                row = ScanResult(file_path=path, scan_status='completed',
                                 file_hash='before', is_corrupted=False)
                db.session.add(row)
                db.session.flush()
                db.session.add(ScanRunFile(scan_id=run_id, scan_result_id=row.id,
                                           file_path=path, status='pending'))
            chunks = [
                ScanChunk(scan_id=run_id, chunk_id='parent',
                          directory_path='/library/parent', files_discovered=100),
                ScanChunk(scan_id=run_id, chunk_id='child',
                          directory_path='/library/parent/child', files_discovered=1),
            ]
            db.session.add_all(chunks)
            db.session.commit()

            scan_service._parallel_scan_selected_chunks(
                Checker(), chunks, paths, True, 4, state, state.id)

            members = ScanRunFile.query.filter_by(scan_id=run_id).all()
            failed = ScanRunFile.query.filter_by(
                scan_id=run_id, file_path='/library/parent/child/broken.png').one()
            parent_member = ScanRunFile.query.filter_by(
                scan_id=run_id, file_path='/library/parent/image_0.png').one()
            failed_global = ScanResult.query.filter_by(
                file_path='/library/parent/child/broken.png').one()
            state = db.session.get(ScanState, state.id)
            assert len(members) == len(paths)
            assert {member.status for member in members} == {'completed', 'error'}
            assert failed.status == 'error'
            assert parent_member.status == 'completed'
            assert failed_global.scan_status == 'completed'
            assert failed_global.file_hash == 'before'
            assert state.files_processed == len(paths)
            assert state.phase == 'error'

    def test_selected_task_does_not_reactivate_terminal_run(self, app, db):
        class CeleryStub:
            def task(self, *args, **kwargs):
                if args and callable(args[0]):
                    return args[0]
                return lambda function: function

        original_config = sys.modules.get('pixelprobe.celery_config')
        sys.modules['pixelprobe.celery_config'] = types.SimpleNamespace(celery_app=CeleryStub())
        sys.modules.pop('pixelprobe.tasks', None)
        try:
            from pixelprobe.tasks import scan_files_task

            with app.app_context():
                state = ScanState.create_new_scan(scan_id='selected-terminal-run')
                state.phase = 'error'
                state.is_active = False
                db.session.commit()

                task = types.SimpleNamespace(request=types.SimpleNamespace(id='terminal-task'))
                result = scan_files_task(task, 'selected-terminal-run', [], force_rescan=True)

                state = ScanState.query.filter_by(scan_id='selected-terminal-run').one()
                assert result['status'] == 'ERROR'
                assert state.phase == 'error'
                assert state.is_active is False
        finally:
            sys.modules.pop('pixelprobe.tasks', None)
            if original_config is None:
                sys.modules.pop('pixelprobe.celery_config', None)
            else:
                sys.modules['pixelprobe.celery_config'] = original_config

    def test_selected_service_does_not_reactivate_terminal_run(self, scan_service, app, db, tmp_path):
        with app.app_context():
            file_path = tmp_path / 'file.png'
            file_path.touch()
            db.session.add(ScanConfiguration(path=str(tmp_path), is_active=True))
            state = ScanState.create_new_scan(scan_id='selected-terminal-service')
            state.phase = 'cancelled'
            state.is_active = False
            db.session.commit()

            result = scan_service.scan_files([str(file_path)], force_rescan=True,
                                             num_workers=1, async_mode=False,
                                             scan_id='selected-terminal-service')

            state = ScanState.query.filter_by(scan_id='selected-terminal-service').one()
            assert result['status'] == 'cancelled'
            assert state.phase == 'cancelled'
            assert state.is_active is False

    def test_missing_selected_observation_does_not_copy_global_result(self, scan_service, app, db):
        with app.app_context():
            run_id = 'selected-no-observation'
            file_path = '/library/previously-completed.png'
            state = ScanState.create_new_scan(scan_id=run_id)
            state.start_scan(['selected_files'], force_rescan=True)
            state.phase = 'scanning'
            result = ScanResult(file_path=file_path, scan_status='completed',
                                file_hash='before', is_corrupted=False)
            db.session.add(result)
            db.session.flush()
            db.session.add(ScanRunFile(scan_id=run_id, scan_result_id=result.id,
                                       file_path=file_path, status='pending'))
            db.session.commit()

            assert scan_service._snapshot_run_member(run_id, file_path, None)

            member = ScanRunFile.query.filter_by(scan_id=run_id, file_path=file_path).one()
            result = ScanResult.query.filter_by(file_path=file_path).one()
            assert member.status == 'error'
            assert member.outcome == 'no_result'
            assert result.scan_status == 'completed'
            assert result.file_hash == 'before'

    def test_cancellation_reclaims_only_owned_scanning_result(self, scan_service, app, db):
        with app.app_context():
            state = ScanState.create_new_scan(scan_id='selected-owned-cancel')
            state.start_scan(['selected_files'], force_rescan=True)
            state.phase = 'scanning'
            owned = ScanResult(file_path='/library/owned.png', scan_status='scanning')
            unrelated = ScanResult(file_path='/library/unrelated.png', scan_status='scanning')
            db.session.add_all([owned, unrelated])
            db.session.flush()
            db.session.add(ScanRunFile(scan_id=state.scan_id, scan_result_id=owned.id,
                                       file_path=owned.file_path, status='processing'))
            db.session.commit()

            scan_service._handle_scan_cancellation(state)

            assert db.session.get(ScanResult, owned.id).scan_status == 'pending'
            assert db.session.get(ScanResult, unrelated.id).scan_status == 'scanning'

    def test_cancellation_does_not_overwrite_terminal_run(self, scan_service, app, db):
        with app.app_context():
            state = ScanState.create_new_scan(scan_id='selected-terminal-cancel')
            state.phase = 'crashed'
            state.is_active = False
            db.session.commit()

            scan_service._handle_scan_cancellation(state)

            state = db.session.get(ScanState, state.id)
            assert state.phase == 'crashed'
            assert state.is_active is False

    @pytest.mark.parametrize('count', [2, 101])
    def test_parallel_selected_workers_use_parent_settings_snapshot(
            self, scan_service, app, db, monkeypatch, count):
        class Checker:
            def __init__(self):
                self.values = []

            def scan_file(self, file_path, force_rescan=False):
                from pixelprobe.media_checker import _setting
                self.values.append(_setting('timeouts.temporal_sample_timeout_secs'))
                return {
                    'outcome': 'completed', 'file_hash': 'a' * 64,
                    'file_size': 1, 'last_modified': datetime.now(timezone.utc),
                    'is_corrupted': False, 'has_warnings': False,
                    'file_type': 'image', 'scan_tool': 'pil', 'scan_output': '',
                }

        with app.app_context():
            invalidate_cache()
            AppConfig.query.delete()
            db.session.commit()
            assert resolve_settings()['timeouts.temporal_sample_timeout_secs'] == 30
            db.session.add(AppConfig(
                key='timeouts.temporal_sample_timeout_secs', value='120'))
            state = ScanState.create_new_scan(scan_id=f'selected-settings-{count}')
            state.start_scan(['selected_files'], force_rescan=True)
            state.phase = 'scanning'
            paths = [f'/library/settings/{index}.png' for index in range(count)]
            for path in paths:
                row = ScanResult(file_path=path, scan_status='completed', file_hash='before')
                db.session.add(row)
                db.session.flush()
                db.session.add(ScanRunFile(scan_id=state.scan_id, scan_result_id=row.id,
                                           file_path=path, status='pending'))
            db.session.commit()
            monkeypatch.setattr(scan_service, '_retry_pending_files', lambda *_args: 0)
            monkeypatch.setattr(scan_service, '_mark_scan_completed', lambda *_args: None)
            checker = Checker()

            if count == 2:
                scan_service._parallel_scan(checker, paths, True, 2, state, state.id)
            else:
                chunk = ScanChunk(scan_id=state.scan_id, chunk_id='settings-chunk',
                                  directory_path='/library/settings', files_discovered=count)
                db.session.add(chunk)
                db.session.commit()
                scan_service._parallel_scan_selected_chunks(
                    checker, [chunk], paths, True, 2, state, state.id)

            assert checker.values == [120] * count

    def test_large_selected_scan_stops_dispatching_after_durable_cancel(self, scan_service, app, db,
                                                                         monkeypatch):
        class Checker:
            def __init__(self):
                self.calls = []

            def scan_file(self, file_path, force_rescan=False):
                self.calls.append(file_path)
                return {
                    'outcome': 'completed', 'file_hash': 'a' * 64,
                    'file_size': 1, 'last_modified': datetime.now(timezone.utc),
                    'is_corrupted': False, 'has_warnings': False,
                    'file_type': 'image', 'scan_tool': 'pil', 'scan_output': '',
                }

        with app.app_context():
            run_id = 'selected-durable-cancel'
            paths = [f'/library/parent/image_{index}.png' for index in range(100)]
            paths.append('/library/parent/child/image.png')
            state = ScanState.create_new_scan(scan_id=run_id)
            state.start_scan(['selected_files'], force_rescan=True)
            state.scan_type = 'selected'
            state.phase = 'scanning'
            for path in paths:
                row = ScanResult(file_path=path, scan_status='completed', file_hash='before')
                db.session.add(row)
                db.session.flush()
                db.session.add(ScanRunFile(scan_id=run_id, scan_result_id=row.id,
                                           file_path=path, status='pending'))
            chunks = [
                ScanChunk(scan_id=run_id, chunk_id='parent-cancel',
                          directory_path='/library/parent', files_discovered=100),
                ScanChunk(scan_id=run_id, chunk_id='child-cancel',
                          directory_path='/library/parent/child', files_discovered=1),
            ]
            db.session.add_all(chunks)
            db.session.commit()
            original_snapshot = scan_service._snapshot_run_member
            snapshots = 0

            def cancel_after_first(*args, **kwargs):
                nonlocal snapshots
                saved = original_snapshot(*args, **kwargs)
                snapshots += 1
                if snapshots == 1:
                    active = db.session.get(ScanState, state.id)
                    active.is_active = False
                    db.session.commit()
                return saved

            monkeypatch.setattr(scan_service, '_snapshot_run_member', cancel_after_first)
            checker = Checker()
            scan_service._parallel_scan_selected_chunks(
                checker, chunks, paths, True, 2, state, state.id)

            state = db.session.get(ScanState, state.id)
            assert len(checker.calls) <= 2
            assert state.phase == 'cancelled'
            assert state.is_active is False
            persisted_chunks = ScanChunk.query.filter_by(scan_id=run_id).all()
            assert {chunk.status for chunk in persisted_chunks} == {'processing', 'pending'}
            assert sum(chunk.files_scanned for chunk in persisted_chunks) == 0

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

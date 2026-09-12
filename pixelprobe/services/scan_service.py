"""
Scan service for handling media scanning operations
"""

import os
import json
import threading
import logging
from datetime import datetime, timezone
import time
from typing import List, Dict, Optional

from flask import current_app
from pixelprobe.constants import SCAN_PHASES
from pixelprobe.media_checker import PixelProbe, load_exclusions_with_patterns
from pixelprobe.models import db, ScanResult, ScanState, ScanChunk, ScanRunFile
from pixelprobe.utils.helpers import ProgressTracker
from pixelprobe.utils.security import get_allowed_scan_paths
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
import hashlib

logger = logging.getLogger(__name__)

class ScanService:
    """Service for managing scan operations"""
    
    def __init__(self, database_uri: str):
        self.database_uri = database_uri
        self.current_scan_thread: Optional[threading.Thread] = None
        self.scan_cancelled = False
        self.scan_cancel_lock = threading.Lock()  # Thread safety for cancellation
        self.scan_progress = {
            'current': 0,
            'total': 0,
            'file': '',
            'status': 'idle'
        }
        self.progress_lock = threading.Lock()
        # Get chunk size from environment or use default
        import os
        self.chunk_size = int(os.environ.get('CHUNK_SIZE', '10000'))  # Files per chunk

    def _selected_result_persistence_guard(self, scan_id):
        """Fence checker cache writes after decode, using its cache session."""
        def guard(session, result):
            state = (session.query(ScanState).filter_by(scan_id=scan_id)
                     .with_for_update().first())
            member = (session.query(ScanRunFile).filter_by(
                scan_id=scan_id, scan_result_id=result.id).with_for_update().first())
            return bool(state and state.is_active and state.phase == SCAN_PHASES['SCANNING']
                        and member and member.status in ('pending', 'processing'))
        return guard
        
    def is_scan_running(self) -> bool:
        """Check if a scan is currently running"""
        # Check thread-based scanning
        thread_running = self.current_scan_thread is not None and self.current_scan_thread.is_alive()

        # Check database for active scan (covers Celery-based scans)
        db_scan_active = False
        try:
            scan_state = ScanState.get_or_create()
            # CRITICAL: Include 'initializing' phase to prevent scans from getting stuck
            # Without this, stuck scans in 'initializing' phase would block new scans forever
            db_scan_active = scan_state.is_active and scan_state.phase in ['initializing', 'discovering', 'adding', 'scanning']
        except Exception as e:
            logger.debug(f"Could not check database scan state: {e}")

        is_running = thread_running or db_scan_active

        logger.debug(f"is_scan_running check: thread_running={thread_running}, "
                    f"db_scan_active={db_scan_active}, result={is_running}")
        return is_running
    
    def get_scan_progress(self) -> Dict:
        """Get current scan progress"""
        with self.progress_lock:
            return self.scan_progress.copy()
    
    def update_progress(self, current: int, total: int, file_path: str, status: str):
        """Update in-memory scan progress"""
        with self.progress_lock:
            self.scan_progress.update({
                'current': current,
                'total': total,
                'file': file_path,
                'status': status
            })
    
    def scan_single_file(self, file_path: str, force_rescan: bool = False,
                         scan_id: Optional[str] = None) -> Dict:
        """Scan a single file.

        When ``scan_id`` is provided (e.g., the API route created a ScanState
        before queueing the Celery task), reuse that row so the UI tracks one
        continuous scan from queued through completed. Without this, a second
        ScanState is created here and the UI's progress monitor briefly sees
        no active scan and flips to "done" before the new row appears.
        """
        from pixelprobe.utils.security import resolve_authorized_media_file
        file_path = resolve_authorized_media_file(file_path)

        # Single file rescans are allowed to run independently
        # They don't check for other running scans since they're quick operations

        # Initialize progress
        self.update_progress(0, 1, file_path, 'scanning')
        self.scan_cancelled = False

        scan_state = None
        if scan_id:
            scan_state = ScanState.query.filter_by(scan_id=scan_id).first()

        if scan_state is None:
            scan_state = ScanState.create_new_scan(scan_id=scan_id)

        # Apply single-file initialization fields directly. We avoid
        # ScanState.start_scan() here because it commits eagerly and sets
        # phase='discovering', which we'd immediately overwrite.
        now = datetime.now(timezone.utc)
        scan_state.is_active = True
        scan_state.phase = SCAN_PHASES['INITIALIZING']
        scan_state.progress_message = 'Initializing single file scan'
        scan_state.estimated_total = 1
        scan_state.phase_total = 1
        scan_state.files_processed = 0
        scan_state.directories = json.dumps([file_path])
        scan_state.force_rescan = force_rescan
        scan_state.error_message = None
        scan_state.start_time = now
        scan_state.last_update = now
        scan_state.end_time = None
        db.session.commit()

        result_row = ScanResult.query.filter_by(file_path=file_path).first()
        if result_row is None:
            result_row = ScanResult(file_path=file_path, scan_status='pending',
                                    discovered_date=datetime.now(timezone.utc),
                                    is_corrupted=None)
            db.session.add(result_row)
            db.session.flush()
        if not ScanRunFile.query.filter_by(scan_id=scan_state.scan_id,
                                           file_path=file_path).first():
            db.session.add(ScanRunFile(scan_id=scan_state.scan_id,
                                       scan_result_id=result_row.id,
                                       file_path=file_path, status='pending'))
        db.session.commit()

        # Capture scan ID for UI progress tracking
        scan_state_id = scan_state.id
        scan_id = scan_state.scan_id

        # Capture Flask app context for the thread
        app = current_app._get_current_object()

        # Create scan thread
        def run_scan():
            checker = None
            # Set up Flask app context for the thread
            with app.app_context():
                try:
                    # Get fresh ScanState object in worker thread to avoid detached instance
                    scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                    if not scan_state:
                        logger.error(f"Could not find scan state with ID {scan_state_id}")
                        return

                    # Update phase to scanning
                    scan_state.phase = 'scanning'
                    scan_state.progress_message = f'Scanning {os.path.basename(file_path)}'
                    db.session.commit()

                    excluded_paths, excluded_extensions, excluded_patterns = load_exclusions_with_patterns()
                    checker = PixelProbe(
                        database_path=self.database_uri,
                        excluded_paths=excluded_paths,
                        excluded_extensions=excluded_extensions,
                        excluded_patterns=excluded_patterns,
                        allowed_paths=get_allowed_scan_paths(),
                        result_persistence_guard=self._selected_result_persistence_guard(
                            scan_state.scan_id),
                    )
                    result = checker.scan_file(file_path, force_rescan=force_rescan)
                    self._snapshot_run_member(scan_state.scan_id, file_path, result)

                    self._mark_scan_completed(scan_state_id, 1, 1)

                    self.update_progress(1, 1, file_path, 'completed')
                    return result
                except Exception as e:
                    logger.error(f"Error scanning file: {e}")

                    # Update scan state to error
                    try:
                        scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                        if scan_state:
                            scan_state.phase = 'error'
                            scan_state.error_message = str(e)
                            scan_state.is_active = False
                            db.session.commit()
                    except Exception as db_error:
                        logger.error(f"Failed to update scan state with error: {db_error}")

                    self.update_progress(1, 1, file_path, 'error')
                    raise
                finally:
                    if checker is not None:
                        checker.dispose_database_connection()
                    # Clear thread reference to allow new scans
                    self.current_scan_thread = None
                    logger.debug("Single file scan thread cleaned up")

        self.current_scan_thread = threading.Thread(target=run_scan, name="SingleFileScan")
        logger.info(f"Starting single file scan thread: {self.current_scan_thread.name}")
        self.current_scan_thread.start()

        return {'status': 'started', 'message': 'Scan started', 'file_path': file_path, 'scan_id': scan_id}
    
    def scan_files(self, file_paths: List[str], force_rescan: bool = False,
                   num_workers: int = 1, async_mode: bool = True,
                   scan_id: Optional[str] = None) -> Dict:
        """Scan specific files only"""
        if self.is_scan_running():
            own_run = ScanState.query.filter_by(scan_id=scan_id, is_active=True).first() if scan_id else None
            if not own_run:
                raise RuntimeError("Another scan is already in progress")

        # Validate files exist - with comprehensive debugging
        import pwd
        import getpass

        try:
            current_user = getpass.getuser()
            current_uid = os.getuid()
            current_gid = os.getgid()
            logger.info(f"scan_files running as user: {current_user} (uid={current_uid}, gid={current_gid})")
        except Exception as e:
            logger.warning(f"Could not determine current user: {e}")

        logger.info(f"Current working directory: {os.getcwd()}")
        logger.info(f"Validating {len(file_paths)} file paths for existence")

        # Log first 3 paths with full details
        for i, path in enumerate(file_paths[:3]):
            logger.info(f"Sample path {i+1}: {path}")
            logger.info(f"  - Is absolute: {os.path.isabs(path)}")
            logger.info(f"  - Exists: {os.path.exists(path)}")
            if os.path.exists(path):
                try:
                    stat_info = os.stat(path)
                    logger.info(f"  - Size: {stat_info.st_size}, Mode: {oct(stat_info.st_mode)}")
                except Exception as e:
                    logger.warning(f"  - Could not stat: {e}")

        from pixelprobe.utils.security import resolve_authorized_media_file
        valid_files = []
        invalid_paths = []
        for path in file_paths:
            try:
                valid_files.append(resolve_authorized_media_file(path))
            except Exception:
                invalid_paths.append(path)
        invalid_count = len(invalid_paths)

        if invalid_count > 0:
            logger.error(f"{invalid_count}/{len(file_paths)} files failed existence check")
            # Log first 3 invalid paths
            invalid_samples = invalid_paths[:3]
            for inv_path in invalid_samples:
                logger.error(f"  Invalid path: {inv_path}")

        if not valid_files:
            logger.error(f"NO valid files found out of {len(file_paths)} provided")
            raise ValueError("No valid files provided")
        
        logger.info(f"Starting scan of {len(valid_files)} specific files")
        
        # Initialize progress
        self.update_progress(0, 0, '', 'initializing')
        self.scan_cancelled = False
        
        # Save scan state
        scan_state = (ScanState.query.filter_by(scan_id=scan_id).first()
                      if scan_id else None)
        if scan_state is None:
            scan_state = ScanState.create_new_scan(scan_id=scan_id)
        scan_state.start_scan(["selected_files"], force_rescan)
        scan_state.scan_type = 'selected'
        # Safely set num_workers if column exists
        if hasattr(scan_state, 'num_workers'):
            scan_state.num_workers = num_workers  # Track the number of workers used
        db.session.commit()
        from pixelprobe.models import ScanResult, ScanRunFile
        for path in valid_files:
            result = ScanResult.query.filter_by(file_path=path).first()
            if result is None:
                result = ScanResult(file_path=path, scan_status='pending',
                                    discovered_date=datetime.now(timezone.utc),
                                    is_corrupted=None)
                db.session.add(result)
                db.session.flush()
            if result and not ScanRunFile.query.filter_by(scan_id=scan_state.scan_id,
                                                          file_path=path).first():
                db.session.add(ScanRunFile(scan_id=scan_state.scan_id,
                                           scan_result_id=result.id, file_path=path,
                                           status='pending'))
        db.session.commit()
        
        # Capture scan ID
        scan_state_id = scan_state.id
        
        # Capture Flask app context for the thread
        app = current_app._get_current_object()
        
        # Create scan thread
        def run_scan():
            checker = None
            with app.app_context():
                try:
                    # Get fresh ScanState object in worker thread
                    scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                    if not scan_state:
                        logger.error(f"Could not find scan state with ID {scan_state_id}")
                        return
                    
                    excluded_paths, excluded_extensions, excluded_patterns = load_exclusions_with_patterns()
                    checker = PixelProbe(
                        database_path=self.database_uri,
                        max_workers=num_workers,  # sizes the checker's DB connection pool
                        excluded_paths=excluded_paths,
                        excluded_extensions=excluded_extensions,
                        excluded_patterns=excluded_patterns,
                        allowed_paths=get_allowed_scan_paths(),
                        result_persistence_guard=self._selected_result_persistence_guard(
                            scan_state.scan_id),
                    )

                    # Skip discovery phase - we already have the files
                    total_files = len(valid_files)
                    logger.info(f"Scanning {total_files} specific files")
                    
                    # For large file lists, use chunking
                    if total_files > 100:
                        # Group files by directory for chunking
                        files_by_dir = {}
                        for file_path in valid_files:
                            dir_path = os.path.dirname(file_path)
                            if dir_path not in files_by_dir:
                                files_by_dir[dir_path] = []
                            files_by_dir[dir_path].append(file_path)
                        
                        # Create chunks for each directory
                        chunks = []
                        for dir_path, files in files_by_dir.items():
                            # Add timestamp to ensure uniqueness
                            timestamp = time.time()
                            chunk_id = hashlib.md5(f"{scan_state.scan_id}:{dir_path}:{timestamp}".encode()).hexdigest()
                            chunk = ScanChunk(
                                scan_id=scan_state.scan_id,
                                chunk_id=chunk_id,
                                directory_path=dir_path,
                                phase='scanning',
                                status='pending'
                            )
                            db.session.add(chunk)
                            chunks.append(chunk)
                        db.session.commit()
                        
                        logger.info(f"Created {len(chunks)} chunks for {total_files} files")

                        # Update scan state
                        # IMPORTANT: Use total_files (not len(chunks)) so UI shows correct file count
                        self.update_progress(0, total_files, '', 'scanning')
                        scan_state.phase = 'scanning'
                        scan_state.phase_number = 3
                        scan_state.phase_current = 0
                        scan_state.phase_total = total_files
                        scan_state.total_chunks = len(chunks)
                        scan_state.start_time = datetime.now(timezone.utc)
                        # Truncate message to avoid VARCHAR limit
                        scan_state.progress_message = f'Scanning {total_files} files in {len(chunks)} dirs'[:200]
                        db.session.commit()
                        
                        # For selected files, we need a special chunk processor
                        if num_workers > 1:
                            self._parallel_scan_selected_chunks(checker, chunks, valid_files, force_rescan, num_workers, scan_state, scan_state_id)
                        else:
                            self._sequential_scan_selected_chunks(checker, chunks, valid_files, force_rescan, scan_state, scan_state_id)
                    else:
                        # For small file lists, use the original method
                        self.update_progress(0, total_files, '', 'scanning')
                        scan_state.phase = 'scanning'
                        scan_state.phase_number = 3
                        scan_state.phase_current = 0
                        scan_state.phase_total = total_files
                        scan_state.start_time = datetime.now(timezone.utc)
                        scan_state.progress_message = f'Scanning {total_files} selected files for corruption...'
                        db.session.commit()
                        
                        if num_workers > 1:
                            self._parallel_scan(checker, valid_files, force_rescan, num_workers, scan_state, scan_state_id)
                        else:
                            self._sequential_scan(checker, valid_files, force_rescan, scan_state, scan_state_id)

                    scan_state = db.session.get(ScanState, scan_state_id)
                    if scan_state and scan_state.is_active:
                        self._mark_scan_completed(scan_state_id, total_files, total_files)
                        
                except Exception as e:
                    logger.error(f"Error during file scan: {e}")
                    self.update_progress(0, 0, '', 'error')
                    scan_state.error_scan(str(e))
                    db.session.commit()
                    raise
                finally:
                    if checker is not None:
                        checker.dispose_database_connection()
                    # Clear thread reference to allow new scans
                    self.current_scan_thread = None
                    logger.info("File scan thread cleaned up")
        
        if async_mode:
            # Run in a separate thread (for direct API calls)
            self.current_scan_thread = threading.Thread(target=run_scan, name="FileListScan")
            logger.info(f"Starting file list scan thread: {self.current_scan_thread.name}")
            self.current_scan_thread.start()
            
            return {
                'status': 'started',
                'message': f'Scan started for {len(valid_files)} files',
                'files': len(valid_files),
                'force_rescan': force_rescan,
                'num_workers': num_workers
            }
        else:
            # Run synchronously (for Celery tasks)
            logger.info("Running file scan synchronously for Celery task")
            try:
                run_scan()
                # Get final scan state for results
                db.session.expire_all()
                final_scan_state = db.session.get(
                    ScanState, scan_state_id, populate_existing=True)
                if final_scan_state:
                    corrupted_found = ScanRunFile.query.filter_by(
                        scan_id=final_scan_state.scan_id, is_corrupted=True).count()
                    phase = final_scan_state.phase
                    completed = phase == SCAN_PHASES['COMPLETED']
                    return {
                        'status': 'completed' if completed else phase,
                        'message': (f'Scan completed for {len(valid_files)} files' if completed
                                    else f'Scan finished with {phase} state'),
                        'files': len(valid_files),
                        'force_rescan': force_rescan,
                        'num_workers': num_workers,
                        'files_processed': final_scan_state.files_processed or 0,
                        'files_scanned': final_scan_state.files_processed or 0,  # For compatibility
                        'files_discovered': final_scan_state.discovery_count or 0,
                        'corrupted_found': corrupted_found,
                        'phase': final_scan_state.phase
                    }
                else:
                    return {
                        'status': 'error',
                        'message': 'Scan result evidence was not persisted',
                        'files': len(valid_files),
                        'force_rescan': force_rescan,
                        'num_workers': num_workers,
                    }
            finally:
                # Ensure thread reference is cleared even in sync mode
                self.current_scan_thread = None
    
    def cancel_scan(self, expected_scan_id: Optional[str] = None) -> Dict:
        """Cancel only work durably owned by the active scan."""
        
        active_phases = ['initializing', 'discovering', 'adding', 'scanning']
        query = ScanState.query.filter(
            ScanState.is_active == True,
            ScanState.phase.in_(active_phases),
        )
        if expected_scan_id:
            query = query.filter(ScanState.scan_id == expected_scan_id)
        scan_state = query.order_by(ScanState.id.desc()).with_for_update().first()
        if not scan_state:
            return {'message': 'No matching active scan is running', 'cancelled': False,
                    'tasks_killed': 0, 'owned_task_count': 0, 'revoked_task_count': 0,
                    'failures': ['no matching active scan']}
        
        logger.info(f"Cancel scan - scan_id: {scan_state.scan_id}, phase: {scan_state.phase}")
        
        failures = []
        # Persist intent first. Workers check this state before claims and
        # between batches, so cancellation remains correct if the broker is down.
        try:
            from pixelprobe.models import ScanChunk, ScanRunFile, ScanTask
            now = datetime.now(timezone.utc)
            scan_state.cancel_requested_at = now
            scan_state.phase = 'cancelled'
            scan_state.is_active = False
            scan_state.end_time = now
            ScanChunk.query.filter(ScanChunk.scan_id == scan_state.scan_id,
                                   ScanChunk.status.in_(['pending', 'processing'])).update(
                {'status': 'cancelled', 'is_complete': True, 'end_time': now},
                synchronize_session=False)
            owned_ids = [row[0] for row in db.session.query(ScanRunFile.scan_result_id).filter(
                ScanRunFile.scan_id == scan_state.scan_id,
                ScanRunFile.status == 'processing').all() if row[0]]
            ScanRunFile.query.filter_by(scan_id=scan_state.scan_id, status='processing').update(
                {'status': 'pending', 'claimed_at': None}, synchronize_session=False)
            if owned_ids:
                ScanResult.query.filter(ScanResult.id.in_(owned_ids),
                                        ScanResult.scan_status == 'scanning').update(
                    {'scan_status': 'pending'}, synchronize_session=False)
            task_ids = [row[0] for row in db.session.query(ScanTask.celery_task_id).filter(
                ScanTask.scan_id == scan_state.scan_id,
                ScanTask.status.in_(['queued', 'dispatched', 'processing'])).all()]
            ScanTask.query.filter(ScanTask.scan_id == scan_state.scan_id,
                                  ScanTask.status.in_(['queued', 'dispatched', 'processing'])).update(
                {'status': 'cancelled', 'completed_at': now}, synchronize_session=False)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return {'message': 'Scan cancellation was not persisted', 'cancelled': False,
                    'failures': [str(e)]}

        # Revoke exactly the persisted task owners. No inspect(), terminate(),
        # or purge: those APIs affect unrelated maintenance work.
        revoked_task_ids = []
        try:
            from pixelprobe.celery_config import celery_app
        except Exception as e:
            celery_app = None
            failures.append(f'broker control unavailable: {e}')
        for task_id in task_ids:
            try:
                if celery_app is None:
                    continue
                celery_app.control.revoke(task_id, terminate=False)
                revoked_task_ids.append(task_id)
            except Exception as e:
                logger.error(f"Failed revoking owned scan task {task_id}: {e}")
                failures.append(f'{task_id}: {e}')
        
        # Step 3: Set cancellation flag and update progress
        self.scan_cancelled = True
        logger.info("Step 3: Set cancellation flag")
        
        # Force progress update to show cancelled state
        self.update_progress(
            self.scan_progress.get('current', 0),
            self.scan_progress.get('total', 0),
            '',
            'cancelled'
        )
        
        # Force thread reference cleanup
        if self.current_scan_thread is not None:
            logger.info("Cleaning up scan thread reference")
            self.current_scan_thread = None
        
        return {
            'message': (f'Cancellation intent recorded; revoke requested for '
                        f'{len(revoked_task_ids)} of {len(task_ids)} owned task(s)'),
            'cancelled': True,
            'tasks_killed': 0,
            'owned_task_count': len(task_ids),
            'revoked_task_count': len(revoked_task_ids),
            'revoke_failures': failures,
            'failures': failures,
        }
    
    def reset_stuck_scans(self) -> Dict:
        """Reset files stuck in scanning state"""
        stuck_results = ScanResult.query.filter_by(scan_status='scanning').all()
        count = len(stuck_results)
        
        for result in stuck_results:
            result.scan_status = 'pending'
            result.error_message = 'Reset from stuck scanning state'
        
        db.session.commit()
        
        return {'message': f'Reset {count} stuck files', 'count': count}
    
    def _sequential_scan(self, checker: PixelProbe, files: List[str], 
                        force_rescan: bool, scan_state: ScanState, scan_state_id: int):
        """Perform sequential scan of files"""
        total_files = len(files)
        
        # Create progress tracker for scan
        progress_tracker = ProgressTracker('scan')
        
        for i, file_path in enumerate(files):
            if self.scan_cancelled:
                break
            
            self.update_progress(i, total_files, file_path, 'scanning')
            
            try:
                result = checker.scan_file(file_path, force_rescan=force_rescan)
                self._snapshot_run_member(scan_state.scan_id, file_path, result)
            except Exception as e:
                logger.error(f"Error scanning file {file_path}: {e}")
            
            # Update scan state progress with error recovery
            try:
                scan_state.update_progress(i + 1, total_files, current_file=file_path)
                
                # Update progress message with current file and ETA
                scan_state.progress_message = progress_tracker.get_progress_message(
                    f'Phase 3 of 3: Scanning {total_files} files for corruption',
                    i + 1,
                    total_files,
                    os.path.basename(file_path)
                )
                db.session.commit()
            except Exception as e:
                logger.error(f"Failed to update progress for file {file_path}: {e}")
                # Try to recover the database session
                try:
                    db.session.rollback()
                    # Re-get scan state and try again
                    scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                    if scan_state:
                        scan_state.update_progress(i + 1, total_files, current_file=file_path)
                        db.session.commit()
                except Exception as e2:
                    logger.error(f"Failed to recover progress update: {e2}")
            
            # Log progress every 10 files for UI debugging
            if (i + 1) % 10 == 0:
                logger.info(f"Scan progress: {i + 1}/{total_files} files processed")
        
        # Complete scan
        if self.scan_cancelled:
            self._handle_scan_cancellation(scan_state)
        else:
            # Retry any files that are still pending before marking complete
            remaining_pending = self._retry_pending_files(checker, force_rescan, scan_state.scan_id)

            self.update_progress(total_files, total_files, '', 'completed')

            # Thread-safe completion using direct SQL update
            self._mark_scan_completed(scan_state_id, total_files, total_files)

            # Create scan report
            completed_scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
            if completed_scan_state:
                scan_type = 'rescan' if force_rescan else 'full_scan'
                self._create_scan_report(completed_scan_state, scan_type=scan_type)

                logger.info(f"=== SCAN COMPLETED (SEQUENTIAL DIRECT) ===")
                logger.info(f"Scan ID: {scan_state_id}")
                logger.info(f"Files scanned: {total_files}")
                if remaining_pending > 0:
                    logger.warning(f"Files still pending after retries: {remaining_pending}")
                logger.info(f"=== END SCAN ===")

    def _parallel_scan(self, checker: PixelProbe, files: List[str],
                      force_rescan: bool, num_workers: int, scan_state: ScanState, scan_state_id: int):
        """Perform parallel scan of files"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        total_files = len(files)
        completed = 0

        # Create progress tracker for scan
        progress_tracker = ProgressTracker('scan')

        # Thread lock for database operations to prevent concurrent session access
        db_lock = threading.Lock()

        def scan_file(file_path):
            if self.scan_cancelled:
                return None
            try:
                return checker.scan_file(file_path, force_rescan=force_rescan)
            except Exception as e:
                logger.error(f"Error scanning {file_path}: {e}")
                return None

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            # Submit all files for scanning
            future_to_file = {executor.submit(scan_file, f): f for f in files}

            # Process completed scans
            for future in as_completed(future_to_file):
                if self.scan_cancelled:
                    executor.shutdown(wait=False)
                    break

                file_path = future_to_file[future]
                completed += 1

                try:
                    result = future.result()
                except Exception as e:
                    logger.error(f"Error scanning {file_path}: {e}")
                    result = None
                with db_lock:
                    self._snapshot_run_member(scan_state.scan_id, file_path, result)

                self.update_progress(completed, total_files, file_path, 'scanning')

                # Update scan state progress with thread-safe database access
                with db_lock:
                    try:
                        scan_state.update_progress(completed, total_files, current_file=file_path)

                        # Update progress message with current file and ETA
                        scan_state.progress_message = progress_tracker.get_progress_message(
                            f'Phase 3 of 3: Scanning {total_files} files for corruption',
                            completed,
                            total_files,
                            os.path.basename(file_path)
                        )
                        db.session.commit()
                    except Exception as e:
                        logger.error(f"Failed to update progress for file {file_path}: {e}")
                        # Try to recover the database session
                        try:
                            db.session.rollback()
                            # Re-get scan state and try again
                            scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                            if scan_state:
                                scan_state.update_progress(completed, total_files, current_file=file_path)
                                db.session.commit()
                        except Exception as e2:
                            logger.error(f"Failed to recover progress update: {e2}")

                # Log progress every 10 files for UI debugging
                if completed % 10 == 0:
                    logger.info(f"Parallel scan progress: {completed}/{total_files} files processed")

        # Complete scan
        if self.scan_cancelled:
            self._handle_scan_cancellation(scan_state)
        else:
            # Retry any files that are still pending before marking complete
            remaining_pending = self._retry_pending_files(checker, force_rescan, scan_state.scan_id)

            self.update_progress(total_files, total_files, '', 'completed')

            # Thread-safe completion using direct SQL update
            self._mark_scan_completed(scan_state_id, total_files, total_files)

            # Create scan report
            completed_scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
            if completed_scan_state:
                scan_type = 'rescan' if force_rescan else 'full_scan'
                self._create_scan_report(completed_scan_state, scan_type=scan_type)

                logger.info(f"=== SCAN COMPLETED (PARALLEL DIRECT) ===")
                logger.info(f"Scan ID: {scan_state_id}")
                logger.info(f"Files scanned: {total_files}")
                if remaining_pending > 0:
                    logger.warning(f"Files still pending after retries: {remaining_pending}")
                logger.info(f"=== END SCAN ===")

    def _mark_scan_completed(self, scan_state_id, files_processed, estimated_total):
        """Finalize against this run's immutable member outcomes."""
        from pixelprobe.services.scan_engine import finalize_scan

        scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).with_for_update().first()
        if not scan_state:
            raise RuntimeError('Scan state disappeared before completion')
        scan_state.files_processed = files_processed
        scan_state.estimated_total = estimated_total
        scan_state.phase_total = estimated_total
        finalize_scan(scan_state)
        db.session.expire_all()

    def _snapshot_run_member(self, scan_id: str, file_path: str, observed=None):
        """Copy the one observed result into the selected run's immutable row."""
        state = (ScanState.query.filter_by(scan_id=scan_id)
                 .with_for_update().first())
        member = (ScanRunFile.query.filter_by(scan_id=scan_id, file_path=file_path)
                  .with_for_update().first())
        if (not state or not member or not state.is_active
                or state.phase != SCAN_PHASES['SCANNING']
                or member.status not in ('pending', 'processing')):
            db.session.commit()
            return False
        row = (ScanResult.query.filter_by(file_path=file_path)
               .populate_existing().first())
        if not row:
            member.status = 'error'
            member.outcome = 'no_result'
            member.error_message = 'Scanner did not persist a result'
        else:
            outcome = (observed or {}).get('outcome') or row.scan_status or 'error'
            if outcome == 'completed' and not (observed or {}).get('file_hash', row.file_hash):
                outcome = 'error'
            source = observed or row
            member.scan_result_id = row.id
            member.status = outcome
            member.outcome = outcome
            get_value = source.get if isinstance(source, dict) else lambda name, default=None: getattr(source, name, default)
            member.file_hash = get_value('file_hash')
            member.file_size = get_value('file_size')
            member.last_modified = get_value('last_modified')
            member.is_corrupted = get_value('is_corrupted')
            member.has_warnings = get_value('has_warnings', False)
            member.corruption_details = get_value('corruption_details')
            member.warning_details = get_value('warning_details')
            member.file_type = get_value('file_type')
            member.scan_tool = get_value('scan_tool')
            member.scan_output = get_value('scan_output')
            member.marked_as_good = row.marked_as_good
            member.error_message = row.error_message
        member.completed_at = datetime.now(timezone.utc)
        db.session.commit()
        return True

    def _create_scan_report(self, scan_state: ScanState, scan_type: str = 'full_scan'):
        """Create a scan report (delegates to shared scan_reporting module)"""
        from pixelprobe.services.scan_reporting import create_scan_report
        create_scan_report(scan_state, scan_type)

    def _retry_pending_files(self, checker: PixelProbe, force_rescan: bool,
                             scan_id: str) -> int:
        """Retry scanning files that are still in 'pending' status.

        This ensures all files get processed in the current scan run before
        marking the scan as complete.

        Args:
            checker: PixelProbe instance to use for scanning
            force_rescan: Whether to force rescan

        Returns:
            int: Number of files that remain pending after retries
        """
        from pixelprobe.models import ScanResult, ScanRunFile

        max_retries = 2

        # Count pending files first (cheap) before deciding to load them
        pending_query = (db.session.query(ScanResult)
                         .join(ScanRunFile, ScanRunFile.scan_result_id == ScanResult.id)
                         .filter(ScanRunFile.scan_id == scan_id,
                                 ScanRunFile.status == 'pending'))
        pending_count = pending_query.count()

        if pending_count == 0:
            return 0

        # Skip retry for large pending sets -- they'll be picked up on the next scan.
        # Loading 90K+ ORM objects blocks completion for hours.
        if pending_count > 1000:
            logger.info(f"{pending_count} files still pending after scan -- will be processed on next scheduled run")
            return pending_count

        pending_files = pending_query.limit(1000).all()

        initial_pending = len(pending_files)
        logger.warning(f"Found {initial_pending} files still pending after initial scan pass - starting retry")

        for retry in range(max_retries):
            pending_count = len(pending_files)
            logger.info(f"Retry {retry + 1}/{max_retries}: Re-scanning {pending_count} pending files")

            files_retried = 0
            for pending_file in pending_files:
                try:
                    # Check if file still exists before retrying
                    if not os.path.exists(pending_file.file_path):
                        logger.warning(f"Pending file no longer exists, marking as missing: {pending_file.file_path}")
                        pending_file.scan_status = 'error'
                        pending_file.scan_output = 'File not found during retry'
                        pending_file.file_exists = False
                        pending_file.error_message = 'File not found during retry'
                        self._snapshot_run_member(scan_id, pending_file.file_path)
                        continue

                    result = checker.scan_file(pending_file.file_path, force_rescan=True)
                    self._snapshot_run_member(scan_id, pending_file.file_path, result)
                    files_retried += 1
                except Exception as e:
                    logger.error(f"Retry failed for {pending_file.file_path}: {e}")

            db.session.commit()
            logger.info(f"Retry {retry + 1}: Attempted to rescan {files_retried} files")

            # Re-check for pending files
            pending_files = (db.session.query(ScanResult)
                             .join(ScanRunFile, ScanRunFile.scan_result_id == ScanResult.id)
                             .filter(ScanRunFile.scan_id == scan_id,
                                     ScanRunFile.status == 'pending').all())

            if not pending_files:
                logger.info(f"All pending files successfully scanned on retry {retry + 1}")
                return 0

        remaining = len(pending_files)
        if remaining > 0:
            # Log details of files that couldn't be processed
            logger.error(f"CRITICAL: {remaining} files still pending after {max_retries} retries")
            for pf in pending_files[:10]:  # Log first 10 for debugging
                logger.error(f"  Still pending: {pf.file_path}")
            if remaining > 10:
                logger.error(f"  ... and {remaining - 10} more")

        return remaining

    def _handle_scan_cancellation(self, scan_state: ScanState):
        """Handle scan cancellation"""
        logger.info(f"=== SCAN CANCELLATION INITIATED ===")
        logger.info(f"Scan ID: {scan_state.scan_id}")
        logger.info(f"Phase at cancellation: {scan_state.phase}")
        logger.info(f"Files processed: {scan_state.files_processed}/{scan_state.estimated_total}")
        
        # Update progress
        self.update_progress(
            self.scan_progress['current'],
            self.scan_progress['total'],
            '',
            'cancelled'
        )
        
        # Update scan state
        scan_state.cancel_scan()
        
        # Clean up any files stuck in 'scanning' state
        stuck_count = ScanResult.reclaim_scanning()

        if stuck_count > 0:
            logger.info(f"Reset {stuck_count} files from 'scanning' to 'pending' state")
        
        db.session.commit()
        logger.info(f"=== SCAN CANCELLATION COMPLETE (ID: {scan_state.scan_id}) ===")
    
    def _sequential_scan_selected_chunks(self, checker: PixelProbe, chunks: List[ScanChunk], 
                                       selected_files: List[str], force_rescan: bool, 
                                       scan_state: ScanState, scan_state_id: int):
        """Scan selected files organized by chunks"""
        # Create a set for fast lookup
        selected_files_set = set(selected_files)
        total_chunks = len(chunks)
        files_scanned = 0
        
        # Create progress tracker
        progress_tracker = ProgressTracker('scan')
        
        for i, chunk in enumerate(chunks):
            if self.scan_cancelled:
                break
            
            # Update chunk status
            chunk.status = 'processing'
            chunk.phase = 'scanning'
            chunk.start_time = datetime.now(timezone.utc)
            db.session.commit()
            
            # Scan only the selected files in this chunk
            chunk_scanned = 0
            for file_path in selected_files:
                if self.scan_cancelled:
                    break
                    
                # Check if file belongs to this chunk's directory
                if file_path.startswith(chunk.directory_path + os.sep) or os.path.dirname(file_path) == chunk.directory_path:
                    try:
                        result = checker.scan_file(file_path, force_rescan=force_rescan)
                        self._snapshot_run_member(scan_state.scan_id, file_path, result)
                        chunk_scanned += 1
                        files_scanned += 1
                        
                        # Update progress
                        self.update_progress(files_scanned, len(selected_files), file_path, 'scanning')
                        
                    except Exception as e:
                        logger.error(f"Error scanning {file_path}: {e}")
            
            # Update chunk completion
            chunk.files_scanned = chunk_scanned
            chunk.status = 'completed'
            chunk.end_time = datetime.now(timezone.utc)
            
            # Update scan state with error recovery
            try:
                scan_state.current_chunk_index = i + 1
                scan_state.update_progress(files_scanned, len(selected_files), current_file='')
                scan_state.progress_message = progress_tracker.get_progress_message(
                    f'Scanning {len(selected_files)} selected files',
                    files_scanned,
                    len(selected_files),
                    os.path.basename(chunk.directory_path) if chunk_scanned > 0 else "Processing..."
                )
                db.session.commit()
            except Exception as e:
                logger.error(f"Failed to update progress for chunk {chunk.directory_path}: {e}")
                # Try to recover the database session
                try:
                    db.session.rollback()
                    # Re-get scan state and try again
                    scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                    if scan_state:
                        scan_state.update_progress(files_scanned, len(selected_files), current_file='')
                        db.session.commit()
                except Exception as e2:
                    logger.error(f"Failed to recover progress update: {e2}")
        
        # Complete scan
        if self.scan_cancelled:
            self._handle_scan_cancellation(scan_state)
        else:
            # Retry any files that are still pending before marking complete
            remaining_pending = self._retry_pending_files(checker, force_rescan, scan_state.scan_id)

            self.update_progress(len(selected_files), len(selected_files), '', 'completed')

            # Thread-safe completion
            self._mark_scan_completed(scan_state_id, len(selected_files), len(selected_files))

            # Create scan report
            completed_scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
            if completed_scan_state:
                scan_type = 'rescan'
                self._create_scan_report(completed_scan_state, scan_type=scan_type)

            logger.info(f"=== SCAN COMPLETED (SEQUENTIAL SELECTED CHUNKS) ===")
            logger.info(f"Scan ID: {scan_state_id}")
            logger.info(f"Files scanned: {len(selected_files)}")
            if remaining_pending > 0:
                logger.warning(f"Files still pending after retries: {remaining_pending}")
            logger.info(f"=== END SCAN ===")

    def _parallel_scan_selected_chunks(self, checker: PixelProbe, chunks: List[ScanChunk],
                                     selected_files: List[str], force_rescan: bool, num_workers: int,
                                     scan_state: ScanState, scan_state_id: int):
        """Parallel scan of selected files organized by chunks"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        # Thread-safe counter and database lock
        files_scanned_lock = threading.Lock()
        db_lock = threading.Lock()
        files_scanned = 0
        selected_files_set = set(selected_files)

        # Create progress tracker
        progress_tracker = ProgressTracker('scan')

        def scan_chunk_files(chunk):
            nonlocal files_scanned
            if self.scan_cancelled:
                return 0

            # Update chunk status with thread-safe database access
            with db_lock:
                chunk.status = 'processing'
                chunk.phase = 'scanning'
                chunk.start_time = datetime.now(timezone.utc)
                db.session.commit()

            chunk_scanned = 0
            # Scan only selected files in this chunk
            for file_path in selected_files:
                if self.scan_cancelled:
                    break

                # Check if file belongs to this chunk
                if file_path.startswith(chunk.directory_path + os.sep) or os.path.dirname(file_path) == chunk.directory_path:
                    try:
                        result = checker.scan_file(file_path, force_rescan=force_rescan)
                        with db_lock:
                            self._snapshot_run_member(scan_state.scan_id, file_path, result)
                        chunk_scanned += 1

                        with files_scanned_lock:
                            files_scanned += 1
                            self.update_progress(files_scanned, len(selected_files), file_path, 'scanning')

                    except Exception as e:
                        logger.error(f"Error scanning {file_path}: {e}")

            # Update chunk completion with thread-safe database access
            with db_lock:
                chunk.files_scanned = chunk_scanned
                chunk.status = 'completed'
                chunk.end_time = datetime.now(timezone.utc)
                db.session.commit()

            return chunk_scanned

        # Process chunks in parallel
        with ThreadPoolExecutor(max_workers=min(num_workers, len(chunks))) as executor:
            future_to_chunk = {executor.submit(scan_chunk_files, chunk): chunk for chunk in chunks}

            completed_chunks = 0
            for future in as_completed(future_to_chunk):
                if self.scan_cancelled:
                    executor.shutdown(wait=False)
                    break

                chunk = future_to_chunk[future]
                completed_chunks += 1

                # Update scan state with thread-safe database access
                with db_lock:
                    try:
                        scan_state.current_chunk_index = completed_chunks
                        scan_state.update_progress(files_scanned, len(selected_files), current_file='')
                        scan_state.progress_message = progress_tracker.get_progress_message(
                            f'Scanning {len(selected_files)} selected files (parallel)',
                            files_scanned,
                            len(selected_files),
                            f"Completed {completed_chunks}/{len(chunks)} directories"
                        )
                        db.session.commit()
                    except Exception as e:
                        logger.error(f"Failed to update progress for chunk {chunk.directory_path}: {e}")
                        # Try to recover the database session
                        try:
                            db.session.rollback()
                            # Re-get scan state and try again
                            scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                            if scan_state:
                                scan_state.update_progress(files_scanned, len(selected_files), current_file='')
                                db.session.commit()
                        except Exception as e2:
                            logger.error(f"Failed to recover progress update: {e2}")
        
        # Complete scan
        if self.scan_cancelled:
            self._handle_scan_cancellation(scan_state)
        else:
            # Retry any files that are still pending before marking complete
            remaining_pending = self._retry_pending_files(checker, force_rescan, scan_state.scan_id)

            self.update_progress(len(selected_files), len(selected_files), '', 'completed')

            # Thread-safe completion
            self._mark_scan_completed(scan_state_id, len(selected_files), len(selected_files))

            # Create scan report
            completed_scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
            if completed_scan_state:
                scan_type = 'rescan'
                self._create_scan_report(completed_scan_state, scan_type=scan_type)

            logger.info(f"=== SCAN COMPLETED (PARALLEL SELECTED CHUNKS) ===")
            logger.info(f"Scan ID: {scan_state_id}")
            logger.info(f"Files scanned: {len(selected_files)}")
            if remaining_pending > 0:
                logger.warning(f"Files still pending after retries: {remaining_pending}")
            logger.info(f"=== END SCAN ===")

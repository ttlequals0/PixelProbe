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
from pixelprobe.models import db, ScanResult, ScanState, ScanChunk
from pixelprobe.utils.helpers import ProgressTracker
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
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

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

        # Capture scan ID for UI progress tracking
        scan_state_id = scan_state.id
        scan_id = scan_state.scan_id

        # Capture Flask app context for the thread
        app = current_app._get_current_object()

        # Create scan thread
        def run_scan():
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
                        excluded_patterns=excluded_patterns
                    )
                    result = checker.scan_file(file_path, force_rescan=force_rescan)

                    # Update scan state to completed
                    scan_state.files_processed = 1
                    scan_state.phase = 'completed'
                    scan_state.progress_message = 'Single file scan completed'
                    scan_state.is_active = False
                    db.session.commit()

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
                    # Clear thread reference to allow new scans
                    self.current_scan_thread = None
                    logger.debug("Single file scan thread cleaned up")

        self.current_scan_thread = threading.Thread(target=run_scan, name="SingleFileScan")
        logger.info(f"Starting single file scan thread: {self.current_scan_thread.name}")
        self.current_scan_thread.start()

        return {'status': 'started', 'message': 'Scan started', 'file_path': file_path, 'scan_id': scan_id}
    
    def scan_files(self, file_paths: List[str], force_rescan: bool = False,
                   num_workers: int = 1, async_mode: bool = True) -> Dict:
        """Scan specific files only"""
        if self.is_scan_running():
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

        valid_files = [f for f in file_paths if os.path.exists(f)]
        invalid_count = len(file_paths) - len(valid_files)

        if invalid_count > 0:
            logger.error(f"{invalid_count}/{len(file_paths)} files failed existence check")
            # Log first 3 invalid paths
            invalid_samples = [f for f in file_paths if not os.path.exists(f)][:3]
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
        scan_state = ScanState.get_or_create()
        scan_state.start_scan(["selected_files"], force_rescan)
        # Safely set num_workers if column exists
        if hasattr(scan_state, 'num_workers'):
            scan_state.num_workers = num_workers  # Track the number of workers used
        db.session.commit()
        
        # Capture scan ID
        scan_state_id = scan_state.id
        
        # Capture Flask app context for the thread
        app = current_app._get_current_object()
        
        # Create scan thread
        def run_scan():
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
                        excluded_patterns=excluded_patterns
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
                        
                except Exception as e:
                    logger.error(f"Error during file scan: {e}")
                    self.update_progress(0, 0, '', 'error')
                    scan_state.error_scan(str(e))
                    db.session.commit()
                    raise
                finally:
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
                final_scan_state = db.session.get(ScanState, scan_state_id)
                if final_scan_state:
                    # Get corrupted file count from ScanResult table with retry logic
                    # Note: ScanResult doesn't have scan_id, so we query all corrupted files
                    from pixelprobe.models import ScanResult

                    # Retry logic for database connection issues
                    max_retries = 3
                    retry_delay = 1  # seconds
                    corrupted_found = 0

                    for attempt in range(max_retries):
                        try:
                            corrupted_found = db.session.query(ScanResult).filter_by(
                                is_corrupted=True
                            ).count()
                            break  # Success, exit retry loop
                        except OperationalError as e:
                            if attempt < max_retries - 1:
                                logger.warning(f"Database connection lost (attempt {attempt + 1}/{max_retries}), retrying in {retry_delay}s: {e}")
                                time.sleep(retry_delay)
                                db.session.rollback()
                                db.session.close()
                                retry_delay *= 2  # Exponential backoff
                            else:
                                logger.error(f"Database connection failed after {max_retries} attempts: {e}")
                                corrupted_found = 0
                    return {
                        'status': 'completed',
                        'message': f'Scan completed for {len(valid_files)} files',
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
                        'status': 'completed',
                        'message': f'Scan completed for {len(valid_files)} files',
                        'files': len(valid_files),
                        'force_rescan': force_rescan,
                                'num_workers': num_workers
                    }
            finally:
                # Ensure thread reference is cleared even in sync mode
                self.current_scan_thread = None
    
    def cancel_scan(self) -> Dict:
        """Cancel the current scan - nuclear option: kill everything"""
        logger.info("cancel_scan() method called - NUCLEAR OPTION")
        
        # Get current scan state
        scan_state = ScanState.get_or_create()
        
        logger.info(f"Cancel scan - scan_id: {scan_state.scan_id}, phase: {scan_state.phase}")
        
        # Step 1: Kill ALL Celery tasks (nuclear option)
        try:
            from pixelprobe.celery_config import celery_app
            
            logger.info("Step 1: Killing ALL Celery tasks")
            
            # Get inspection object
            inspect = celery_app.control.inspect()
            
            # Kill ALL active tasks on ALL workers
            active = inspect.active()
            if active:
                task_count = 0
                for worker_name, tasks in active.items():
                    logger.info(f"Killing {len(tasks)} tasks on worker {worker_name}")
                    for task in tasks:
                        task_id = task.get('id')
                        celery_app.control.revoke(task_id, terminate=True, signal='SIGKILL')
                        task_count += 1
                logger.info(f"Killed {task_count} active tasks")
            
            # Revoke ALL reserved/queued tasks
            reserved = inspect.reserved()
            if reserved:
                task_count = 0
                for worker_name, tasks in reserved.items():
                    logger.info(f"Revoking {len(tasks)} reserved tasks on worker {worker_name}")
                    for task in tasks:
                        task_id = task.get('id')
                        celery_app.control.revoke(task_id, terminate=False)
                        task_count += 1
                logger.info(f"Revoked {task_count} reserved tasks")
            
            # Purge the entire queue
            celery_app.control.purge()
            logger.info("Purged entire Celery queue")
            
        except Exception as e:
            logger.error(f"Error killing Celery tasks: {e}")
        
        # Step 2: Clean up database state
        logger.info("Step 2: Cleaning up database state")
        
        try:
            from pixelprobe.models import ScanChunk
            
            # Mark ALL chunks as cancelled
            chunks_updated = db.session.query(ScanChunk).filter(
                ScanChunk.scan_id == scan_state.scan_id,
                ScanChunk.status.in_(['pending', 'processing', 'queued'])
            ).update({
                'status': 'cancelled',
                'end_time': datetime.now(timezone.utc)
            }, synchronize_session=False)
            
            logger.info(f"Marked {chunks_updated} chunks as cancelled")
            
            # Reset any files stuck in 'scanning' status
            files_reset = ScanResult.reclaim_scanning()

            logger.info(f"Reset {files_reset} files from 'scanning' to 'pending'")
        
            # Cancel the scan state
            scan_state.cancel_scan()
            
            # Commit all changes
            db.session.commit()
            
        except Exception as e:
            logger.error(f"Error cleaning up database: {e}")
        
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
        
        logger.info("=== SCAN CANCELLATION COMPLETE (NUCLEAR) ===")
        
        return {
            'message': 'Scan cancellation completed - all tasks killed',
            'tasks_killed': True,
            'database_cleaned': True
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
                checker.scan_file(file_path, force_rescan=force_rescan)
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
            remaining_pending = self._retry_pending_files(checker, force_rescan)

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
            remaining_pending = self._retry_pending_files(checker, force_rescan)

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
        """Thread-safe scan completion using direct SQL update."""
        db.session.execute(
            text("""UPDATE scan_state SET phase = 'completed', is_active = false, end_time = :end_time,
                    files_processed = :files_processed, estimated_total = :estimated_total,
                    progress_message = 'Scan completed'
                WHERE id = :id"""),
            {
                'end_time': datetime.now(timezone.utc),
                'id': scan_state_id,
                'files_processed': files_processed,
                'estimated_total': estimated_total,
            }
        )
        db.session.commit()
        # Expire all cached ORM objects so subsequent queries/commits
        # read the updated state from PostgreSQL instead of writing
        # stale is_active=True back from the identity map
        db.session.expire_all()

    def _create_scan_report(self, scan_state: ScanState, scan_type: str = 'full_scan'):
        """Create a scan report (delegates to shared scan_reporting module)"""
        from pixelprobe.services.scan_reporting import create_scan_report
        create_scan_report(scan_state, scan_type)

    def _retry_pending_files(self, checker: PixelProbe, force_rescan: bool) -> int:
        """Retry scanning files that are still in 'pending' status.

        This ensures all files get processed in the current scan run before
        marking the scan as complete.

        Args:
            checker: PixelProbe instance to use for scanning
            force_rescan: Whether to force rescan

        Returns:
            int: Number of files that remain pending after retries
        """
        from pixelprobe.models import ScanResult

        max_retries = 2

        # Count pending files first (cheap) before deciding to load them
        pending_count = db.session.query(ScanResult).filter(
            ScanResult.scan_status == 'pending'
        ).count()

        if pending_count == 0:
            return 0

        # Skip retry for large pending sets -- they'll be picked up on the next scan.
        # Loading 90K+ ORM objects blocks completion for hours.
        if pending_count > 1000:
            logger.info(f"{pending_count} files still pending after scan -- will be processed on next scheduled run")
            return pending_count

        pending_files = db.session.query(ScanResult).filter(
            ScanResult.scan_status == 'pending'
        ).limit(1000).all()

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
                        continue

                    checker.scan_file(pending_file.file_path, force_rescan=True)
                    files_retried += 1
                except Exception as e:
                    logger.error(f"Retry failed for {pending_file.file_path}: {e}")

            db.session.commit()
            logger.info(f"Retry {retry + 1}: Attempted to rescan {files_retried} files")

            # Re-check for pending files
            pending_files = db.session.query(ScanResult).filter(
                ScanResult.scan_status == 'pending'
            ).all()

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
                        checker.scan_file(file_path, force_rescan=force_rescan)
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
            remaining_pending = self._retry_pending_files(checker, force_rescan)

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
                        checker.scan_file(file_path, force_rescan=force_rescan)
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
            remaining_pending = self._retry_pending_files(checker, force_rescan)

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
"""
Maintenance service for cleanup and file monitoring operations
"""

import os
import threading
import time
import logging
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional
import uuid

from media_checker import PixelProbe, load_exclusions, load_exclusions_with_patterns
from models import db, ScanResult, CleanupState, FileChangesState, ScanReport
from utils import ProgressTracker

logger = logging.getLogger(__name__)

class MaintenanceService:
    """Service for maintenance operations like cleanup and file monitoring"""
    
    def __init__(self, database_uri: str):
        self.database_uri = database_uri
        self.cleanup_thread: Optional[threading.Thread] = None
        self.file_changes_thread: Optional[threading.Thread] = None
        
        # Cleanup state
        self.cleanup_state = {
            'is_running': False,
            'phase': 'idle',
            'files_processed': 0,
            'total_files': 0,
            'orphaned_found': 0,
            'progress_percentage': 0,
            'start_time': None,
            'cancel_requested': False
        }
        self.cleanup_lock = threading.Lock()
        
        # File changes state
        self.file_changes_state = {
            'is_running': False,
            'phase': 'idle',
            'files_processed': 0,
            'total_files': 0,
            'changes_found': 0,
            'corrupted_found': 0,
            'progress_percentage': 0,
            'start_time': None,
            'cancel_requested': False
        }
        self.file_changes_lock = threading.Lock()
    
    def start_cleanup(self) -> Dict:
        """Start cleanup of orphaned database entries"""
        from flask import current_app

        if self.cleanup_thread and self.cleanup_thread.is_alive():
            raise RuntimeError("Cleanup operation already in progress")

        # Reset state
        with self.cleanup_lock:
            self.cleanup_state.update({
                'is_running': True,
                'phase': 'starting',
                'files_processed': 0,
                'total_files': 0,
                'orphaned_found': 0,
                'progress_percentage': 0,
                'start_time': time.time(),
                'cancel_requested': False
            })

        # Create cleanup state in database - ensure we have app context
        try:
            cleanup_record = CleanupState(
                start_time=datetime.now(timezone.utc),
                is_active=True,
                phase='starting',
                phase_number=1
            )
            db.session.add(cleanup_record)
            db.session.commit()
            cleanup_id = cleanup_record.id
        except Exception as e:
            logger.error(f"Error creating cleanup record: {str(e)}")
            # If we can't create DB record, still start cleanup with a UUID
            cleanup_id = str(uuid.uuid4())

        # Get app instance for thread context
        app = current_app._get_current_object()

        # Start cleanup in background with app context
        self.cleanup_thread = threading.Thread(
            target=self._run_cleanup_with_context,
            args=(app, cleanup_id)
        )
        self.cleanup_thread.start()

        return {
            'message': 'Cleanup operation started',
            'cleanup_id': cleanup_id
        }
    
    def start_file_changes_check(self) -> Dict:
        """Start checking for file changes"""
        from flask import current_app

        if self.file_changes_thread and self.file_changes_thread.is_alive():
            raise RuntimeError("File changes check already in progress")

        # Create unique check ID
        check_id = str(uuid.uuid4())

        # Reset state
        with self.file_changes_lock:
            self.file_changes_state.update({
                'is_running': True,
                'phase': 'starting',
                'files_processed': 0,
                'total_files': 0,
                'changes_found': 0,
                'corrupted_found': 0,
                'progress_percentage': 0,
                'start_time': time.time(),
                'cancel_requested': False
            })

        # Create file changes state in database - ensure we have app context
        try:
            file_changes_record = FileChangesState(
                check_id=check_id,
                start_time=datetime.now(timezone.utc),
                is_active=True,
                phase='starting',
                phase_number=1
            )
            db.session.add(file_changes_record)
            db.session.commit()
        except Exception as e:
            logger.error(f"Error creating file changes record: {str(e)}")

        # Get app instance for thread context
        app = current_app._get_current_object()

        # Start file changes check in background with app context
        self.file_changes_thread = threading.Thread(
            target=self._run_file_changes_check_with_context,
            args=(app, check_id)
        )
        self.file_changes_thread.start()

        return {
            'message': 'File changes check started',
            'check_id': check_id
        }
    
    def get_cleanup_status(self) -> Dict:
        """Get current cleanup status"""
        with self.cleanup_lock:
            return self.cleanup_state.copy()
    
    def get_file_changes_status(self) -> Dict:
        """Get current file changes check status"""
        with self.file_changes_lock:
            return self.file_changes_state.copy()
    
    def cancel_cleanup(self) -> Dict:
        """Cancel the current cleanup operation"""
        cleanup_record = CleanupState.query.order_by(CleanupState.id.desc()).first()
        
        if cleanup_record and cleanup_record.is_active:
            if hasattr(cleanup_record, 'cancel_requested'):
                cleanup_record.cancel_requested = True
            cleanup_record.progress_message = 'Cancellation requested...'
            db.session.commit()
            
            with self.cleanup_lock:
                self.cleanup_state['cancel_requested'] = True
            
            return {'message': 'Cleanup cancellation requested'}
        else:
            raise RuntimeError("No active cleanup operation to cancel")
    
    def cancel_file_changes(self) -> Dict:
        """Cancel the current file changes check"""
        file_changes_record = FileChangesState.query.order_by(FileChangesState.id.desc()).first()
        
        if file_changes_record and file_changes_record.is_active:
            if hasattr(file_changes_record, 'cancel_requested'):
                file_changes_record.cancel_requested = True
            file_changes_record.progress_message = 'Cancellation requested...'
            db.session.commit()
            
            with self.file_changes_lock:
                self.file_changes_state['cancel_requested'] = True
            
            return {'message': 'File changes check cancellation requested'}
        else:
            raise RuntimeError("No active file changes check to cancel")
    
    def reset_cleanup_state(self) -> Dict:
        """Force reset cleanup state"""
        # Mark all active cleanups as failed
        active_cleanups = CleanupState.query.filter_by(is_active=True).all()
        for cleanup in active_cleanups:
            cleanup.is_active = False
            cleanup.phase = 'failed'
            cleanup.end_time = datetime.now(timezone.utc)
            cleanup.progress_message = 'Force reset by user'
        
        db.session.commit()
        
        # Reset in-memory state
        with self.cleanup_lock:
            self.cleanup_state.update({
                'is_running': False,
                'phase': 'idle',
                'files_processed': 0,
                'total_files': 0,
                'orphaned_found': 0,
                'progress_percentage': 0,
                'start_time': None,
                'cancel_requested': False
            })
        
        return {'message': 'Cleanup state reset successfully'}
    
    def _run_cleanup_with_context(self, app, cleanup_id):
        """Run cleanup with app context"""
        with app.app_context():
            self._run_cleanup(cleanup_id)

    def _run_file_changes_check_with_context(self, app, check_id):
        """Run file changes check with app context"""
        with app.app_context():
            self._run_file_changes_check(check_id)

    def _run_cleanup(self, cleanup_id, file_paths=None):
        """Run the cleanup operation

        Args:
            cleanup_id: ID of the cleanup record
            file_paths: Optional list of specific file paths to check (if None, checks all files)
        """
        try:
            cleanup_record = CleanupState.query.get(cleanup_id)
            if not cleanup_record:
                logger.error(f"Cleanup record not found: {cleanup_id}")
                return

            # Keep track of orphaned files for the report
            self.orphaned_files_list = []

            # Phase 1: Scanning database
            cleanup_record.phase = 'scanning_database'
            cleanup_record.phase_number = 1

            # Get database entries - either all or filtered by file_paths
            if file_paths:
                cleanup_record.progress_message = f'Phase 1 of 3: Scanning {len(file_paths)} specific file(s) in database...'
                db.session.commit()
                # Filter to only the specified file paths
                all_results = ScanResult.query.filter(ScanResult.file_path.in_(file_paths)).all()
                logger.info(f"Cleanup scoped to {len(file_paths)} specific file(s), found {len(all_results)} in database")
            else:
                cleanup_record.progress_message = 'Phase 1 of 3: Scanning database entries...'
                db.session.commit()
                # Get all database entries
                all_results = ScanResult.query.all()
                logger.info(f"Cleanup scanning all {len(all_results)} files in database")

            total_files = len(all_results)
            
            cleanup_record.total_files = total_files
            cleanup_record.phase_total = total_files
            db.session.commit()
            
            with self.cleanup_lock:
                self.cleanup_state['total_files'] = total_files
                self.cleanup_state['phase'] = 'scanning_database'
            
            # Phase 2: Checking and collecting orphaned files
            cleanup_record.phase = 'checking_files'
            cleanup_record.phase_number = 2
            cleanup_record.progress_message = f'Phase 2 of 3: Checking {total_files} files on filesystem...'
            db.session.commit()
            
            # Create progress tracker for cleanup
            progress_tracker = ProgressTracker('cleanup')
            
            orphaned_ids = []  # Store IDs instead of objects
            orphaned_paths = []  # Store paths for logging
            orphaned_count = 0

            for i, result in enumerate(all_results):
                if self._is_cancelled(cleanup_record):
                    break

                # Update progress
                cleanup_record.files_processed = i + 1
                cleanup_record.phase_current = i + 1
                cleanup_record.current_file = result.file_path

                # Update progress message with current file and ETA
                cleanup_record.progress_message = progress_tracker.get_progress_message(
                    f'Phase 2 of 3: Checking {total_files} files on filesystem',
                    i + 1,
                    total_files,
                    os.path.basename(result.file_path)
                )

                # Check if file exists - use multiple methods for robust detection
                file_exists = False
                try:
                    # Method 1: os.path.exists() - fast but may have issues with symlinks
                    if os.path.exists(result.file_path):
                        file_exists = True
                    # Method 2: Try to stat the file directly - more reliable
                    elif os.path.isfile(result.file_path):
                        file_exists = True
                    # Method 3: Check if path exists at all (directory or file)
                    elif os.path.lexists(result.file_path):
                        # lexists returns True even for broken symlinks
                        # If lexists is True but exists is False, it's a broken symlink - treat as orphan
                        file_exists = False
                        logger.info(f"Found broken symlink or inaccessible file: {result.file_path}")
                except (OSError, IOError) as e:
                    # If we get an error accessing the file, treat it as orphaned
                    logger.warning(f"Error accessing file {result.file_path}: {e} - treating as orphan")
                    file_exists = False

                if not file_exists:
                    orphaned_ids.append(result.id)  # Store ID instead of object
                    orphaned_paths.append(result.file_path)  # Store path for logging
                    orphaned_count += 1
                    cleanup_record.orphaned_found = orphaned_count
                    logger.info(f"Found orphaned entry: {result.file_path}")
                    # Store for report
                    self.orphaned_files_list.append(result.file_path)
                
                # Update progress periodically
                if i % 100 == 0:
                    cleanup_record.files_processed = i + 1
                    db.session.commit()
                    
                with self.cleanup_lock:
                    self.cleanup_state['files_processed'] = i + 1
                    self.cleanup_state['orphaned_found'] = orphaned_count
            
            # Check if cancelled before proceeding to deletion phase
            if self._is_cancelled(cleanup_record):
                logger.info("Cleanup cancelled before deletion phase")
                cleanup_record.phase = 'cancelled'
                cleanup_record.progress_message = 'Cleanup cancelled by user'
                cleanup_record.is_active = False
                cleanup_record.end_time = datetime.now(timezone.utc)
                db.session.commit()
                
                with self.cleanup_lock:
                    self.cleanup_state['is_running'] = False
                    self.cleanup_state['phase'] = 'cancelled'
                return
            
            # Phase 3: Delete orphaned entries from database
            if orphaned_ids:
                cleanup_record.phase = 'deleting_entries'
                cleanup_record.phase_number = 3
                cleanup_record.progress_message = f'Phase 3 of 3: Removing {orphaned_count} orphaned entries from database...'
                cleanup_record.total_files = len(orphaned_ids)
                cleanup_record.phase_total = len(orphaned_ids)
                cleanup_record.files_processed = 0
                cleanup_record.phase_current = 0
                db.session.commit()

                # Delete orphaned entries in batches for performance
                deleted_count = 0
                batch_size = 50

                for i in range(0, len(orphaned_ids), batch_size):
                    if self._is_cancelled(cleanup_record):
                        break

                    batch_ids = orphaned_ids[i:i + batch_size]
                    batch_paths = orphaned_paths[i:i + batch_size]

                    # Delete by IDs to avoid detached instance issues
                    ScanResult.query.filter(ScanResult.id.in_(batch_ids)).delete(synchronize_session=False)

                    # Log the deletions
                    for path in batch_paths:
                        deleted_count += 1
                        logger.info(f"Deleted orphaned entry: {path}")

                    # Commit batch
                    db.session.commit()
                    
                    # Update progress
                    cleanup_record.files_processed = deleted_count
                    cleanup_record.phase_current = deleted_count
                    cleanup_record.current_file = f"Deleted {deleted_count}/{orphaned_count} entries"
                    db.session.commit()
                    
                    with self.cleanup_lock:
                        self.cleanup_state['files_processed'] = deleted_count
                
                logger.info(f"Successfully deleted {deleted_count} orphaned database entries")
            
            # Final commit
            db.session.commit()
            
            # Complete cleanup
            if self._is_cancelled(cleanup_record):
                cleanup_record.phase = 'cancelled'
                cleanup_record.progress_message = 'Cleanup cancelled by user'
            else:
                cleanup_record.phase = 'complete'
                if orphaned_count > 0:
                    deleted_count = len(orphaned_ids) if orphaned_ids else orphaned_count
                    cleanup_record.progress_message = f'Cleanup complete. Deleted {deleted_count} orphaned database entries.'
                else:
                    cleanup_record.progress_message = 'Cleanup complete. No orphaned entries found.'
            
            cleanup_record.is_active = False
            cleanup_record.end_time = datetime.now(timezone.utc)
            db.session.commit()
            
            # Create scan report for cleanup operation
            # Always try to create a report even if there was an error, as long as we have some data
            if cleanup_record.phase in ('complete', 'error'):
                self._create_cleanup_report(cleanup_record, getattr(self, 'orphaned_files_list', []))
            
            with self.cleanup_lock:
                self.cleanup_state['is_running'] = False
                self.cleanup_state['phase'] = cleanup_record.phase
                
        except Exception as e:
            logger.error(f"Error during cleanup: {e}", exc_info=True)  # Add stack trace
            self._handle_cleanup_error(cleanup_id, str(e))

            # Try to create error report
            try:
                cleanup_record = CleanupState.query.get(cleanup_id)
                if cleanup_record:
                    self._create_cleanup_report(cleanup_record, getattr(self, 'orphaned_files_list', []))
            except Exception as report_error:
                logger.error(f"Failed to create error report: {report_error}")
    
    def _create_cleanup_report(self, cleanup_record: CleanupState, orphaned_files_list=None):
        """Create a report for the cleanup operation"""
        try:
            # Calculate duration
            duration_seconds = None
            if cleanup_record.start_time and cleanup_record.end_time:
                duration_seconds = (cleanup_record.end_time - cleanup_record.start_time).total_seconds()

            # Create the report
            report = ScanReport(
                scan_type='cleanup',
                start_time=cleanup_record.start_time,
                end_time=cleanup_record.end_time,
                duration_seconds=duration_seconds,
                status='completed' if cleanup_record.phase == 'complete' else 'cancelled',
                total_files_discovered=cleanup_record.total_files,
                files_scanned=cleanup_record.files_processed,
                orphaned_records_found=cleanup_record.orphaned_found,
                orphaned_records_deleted=cleanup_record.orphaned_found,  # All found orphans are deleted
                created_at=datetime.now(timezone.utc)
            )

            # Store the list of orphaned files in directories_scanned field as JSON
            # This field is repurposed for cleanup reports to store the orphaned files list
            if orphaned_files_list:
                import json
                report.directories_scanned = json.dumps(orphaned_files_list)
            
            db.session.add(report)
            db.session.commit()
            
            logger.info(f"Created cleanup report {report.report_id} for cleanup operation")
            return report
            
        except Exception as e:
            logger.error(f"Failed to create cleanup report: {e}")
            # Don't fail the cleanup operation if report creation fails
            return None
    
    def _run_file_changes_check(self, check_id: str, file_paths=None):
        """Run the file changes check operation

        Args:
            check_id: Unique ID for this check
            file_paths: Optional list of specific file paths to check (if None, checks all files)
        """
        try:
            # Use READ COMMITTED isolation level to reduce lock contention
            # This allows reads to see committed data without holding locks
            from sqlalchemy import text
            db.session.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL READ COMMITTED"))
            logger.info("Set transaction isolation level to READ COMMITTED for file changes check")

            file_changes_record = FileChangesState.query.filter_by(check_id=check_id).first()
            if not file_changes_record:
                logger.error(f"File changes record not found: {check_id}")
                return

            # Keep track of changed files for the report
            self.changed_files_list = []

            # Phase 1: Starting
            file_changes_record.phase = 'starting'
            file_changes_record.phase_number = 1
            file_changes_record.phase_total = 1
            file_changes_record.phase_current = 0

            # Get total count - either all files or filtered by file_paths
            if file_paths:
                file_changes_record.progress_message = f'Phase 1 of 3: Starting file changes check for {len(file_paths)} specific file(s)...'
                db.session.commit()
                total_files = ScanResult.query.filter(ScanResult.file_path.in_(file_paths)).count()
                logger.info(f"File changes check scoped to {len(file_paths)} specific file(s), found {total_files} in database")
            else:
                file_changes_record.progress_message = 'Phase 1 of 3: Starting file changes check...'
                db.session.commit()
                total_files = ScanResult.query.count()
                logger.info(f"File changes check scanning all {total_files} files in database")

            file_changes_record.total_files = total_files
            file_changes_record.phase_current = 1
            db.session.commit()
            
            # Phase 2a: Dispatching parallel hash calculation tasks
            file_changes_record.phase = 'dispatching_tasks'
            file_changes_record.phase_number = 2
            file_changes_record.phase_total = total_files
            file_changes_record.phase_current = 0
            file_changes_record.files_processed = 0
            file_changes_record.progress_message = f'Phase 2a of 3: Queuing hash calculation tasks - 0 / {total_files:,} (0%)'
            file_changes_record.last_heartbeat = datetime.now(timezone.utc)
            db.session.commit()

            logger.info(f"Starting Phase 2a: parallel hash calculation for {total_files} files using Celery workers")

            # Import Celery task
            from pixelprobe.tasks import calculate_file_hash_task
            from celery import group

            # Track dispatched tasks and changed files
            task_results = []
            changed_files = []

            # OPTIMIZATION: Load all files at once (like cleanup does) to avoid pagination issues
            # This is more memory-intensive but eliminates the progressive slowdown problem
            # with ID-based pagination on large datasets
            logger.info(f"Loading all {total_files} files from database in a single query...")

            # Get database entries - either all or filtered by file_paths (same as cleanup)
            if file_paths:
                all_results = ScanResult.query.filter(ScanResult.file_path.in_(file_paths)).all()
                logger.info(f"Loaded {len(all_results)} specific files from database")
            else:
                all_results = ScanResult.query.all()
                logger.info(f"Loaded all {len(all_results)} files from database")

            # Phase 2a: Queue all hash calculation tasks in batches for better performance
            files_queued = 0
            last_heartbeat_time = time.time()

            # CRITICAL FIX: Batch task submission for performance with 1M+ files
            # Submitting tasks one-by-one is extremely slow and causes task starvation
            # v2.4.57: Reduced batch size from 1000 to 100 to prevent memory exhaustion
            batch_size = 100  # Smaller batches to avoid SIGBUS/memory issues
            task_batch = []
            batch_submit_delay = 0.1  # 100ms delay between batches to avoid overwhelming system

            logger.info(f"Dispatching hash calculation tasks for {len(all_results)} files in batches of {batch_size}...")

            # Iterate through in-memory list and queue Celery tasks in batches
            for i, result in enumerate(all_results):
                # Heartbeat every 30 seconds
                current_time = time.time()
                if current_time - last_heartbeat_time >= 30:
                    file_changes_record.last_heartbeat = datetime.now(timezone.utc)
                    logger.info(f"Phase 2a heartbeat: {files_queued}/{len(all_results)} tasks queued")
                    db.session.commit()
                    last_heartbeat_time = current_time

                if self._is_cancelled_file_changes(file_changes_record):
                    # Submit any remaining tasks in batch before cancelling
                    if task_batch:
                        logger.info(f"Submitting final batch of {len(task_batch)} tasks before cancellation")
                        batch_group = group(*task_batch)
                        batch_result = batch_group.apply_async()
                        for task in batch_result:
                            task_results.append(task)
                    logger.info(f"File changes check cancelled at {files_queued}/{len(all_results)} files queued")
                    break

                # Convert stored_modified to ISO format for serialization
                stored_modified_iso = result.last_modified.isoformat() if result.last_modified else None

                # Add task to batch instead of submitting immediately
                task = calculate_file_hash_task.s(
                    file_id=result.id,
                    file_path=result.file_path,
                    stored_hash=result.file_hash,
                    stored_modified=stored_modified_iso
                )
                task_batch.append(task)
                files_queued += 1

                # Submit batch when it reaches batch_size
                if len(task_batch) >= batch_size:
                    try:
                        logger.debug(f"Submitting batch of {len(task_batch)} tasks")
                        batch_group = group(*task_batch)
                        batch_result = batch_group.apply_async()
                        # Add individual task results to our tracking list
                        for task in batch_result:
                            task_results.append(task)
                        task_batch = []  # Reset batch

                        # Small delay between batches to avoid overwhelming system
                        time.sleep(batch_submit_delay)
                    except Exception as e:
                        logger.error(f"Error submitting batch at file {files_queued}: {e}")
                        # Try to recover by splitting batch in half and retrying
                        if len(task_batch) > 10:
                            logger.info("Retrying with smaller batch size")
                            half_batch = task_batch[:len(task_batch)//2]
                            try:
                                batch_group = group(*half_batch)
                                batch_result = batch_group.apply_async()
                                for task in batch_result:
                                    task_results.append(task)
                                # Keep second half for next iteration
                                task_batch = task_batch[len(task_batch)//2:]
                            except:
                                logger.error("Failed to submit even half batch, skipping")
                                task_batch = []

                # Update progress every 10000 files (reduce DB commit overhead)
                if files_queued % 10000 == 0:
                    file_changes_record.phase_current = files_queued
                    pct = int((files_queued / len(all_results) * 100)) if len(all_results) > 0 else 0
                    file_changes_record.progress_message = f'Phase 2a of 3: Queuing hash calculation tasks - {files_queued:,} / {len(all_results):,} ({pct}%)'
                    logger.info(f"Phase 2a: Queued {files_queued}/{len(all_results)} hash calculation tasks")
                    db.session.commit()

            # Submit any remaining tasks in the final batch
            if task_batch:
                try:
                    logger.info(f"Submitting final batch of {len(task_batch)} tasks")
                    batch_group = group(*task_batch)
                    batch_result = batch_group.apply_async()
                    for task in batch_result:
                        task_results.append(task)
                except Exception as e:
                    logger.error(f"Error submitting final batch: {e}")
                    # Try to submit tasks individually as last resort
                    logger.info("Attempting to submit final batch tasks individually")
                    for single_task in task_batch:
                        try:
                            result = single_task.apply_async()
                            task_results.append(result)
                        except Exception as task_error:
                            logger.error(f"Failed to submit individual task: {task_error}")

            logger.info(f"Phase 2a complete: Queued {len(task_results)} hash calculation tasks (files_queued={files_queued}, expected={total_files})")

            # Phase 2b: Collect results from workers
            file_changes_record.phase = 'collecting_results'
            file_changes_record.phase_total = len(task_results)
            file_changes_record.phase_current = 0
            file_changes_record.progress_message = f'Phase 2b of 3: Collecting hash calculation results - 0 / {len(task_results):,} (0%)'
            file_changes_record.last_heartbeat = datetime.now(timezone.utc)
            db.session.commit()

            logger.info(f"Starting Phase 2b: Collecting results from {len(task_results)} hash calculation tasks")

            # Create progress tracker
            progress_tracker = ProgressTracker('file_changes')
            results_collected = 0
            last_heartbeat_time = time.time()

            # OPTIMIZATION: Multi-batch checking to handle out-of-order task completion
            # Tasks complete based on file size, NOT queue order! Small files anywhere
            # in the queue complete before large files at the start of the queue.
            # We must check multiple batches to catch these out-of-order completions.
            pending_tasks = list(task_results)
            check_batch_size = 10000  # Size of each batch
            batches_per_iteration = 5  # Check 5 batches (50K tasks) per iteration
            batch_start_offset = 0  # Rotating start position for fairness
            phase_2b_start_time = time.time()

            logger.info(f"Phase 2b: Multi-batch collection using {batches_per_iteration} batches of {check_batch_size} tasks each")

            while pending_tasks:
                # Heartbeat every 30 seconds - update UI and database
                current_time = time.time()
                if current_time - last_heartbeat_time >= 30:
                    file_changes_record.last_heartbeat = datetime.now(timezone.utc)
                    # Update progress for UI even if we haven't hit 100 result milestone
                    file_changes_record.phase_current = results_collected

                    # Calculate rate and ETA
                    elapsed = current_time - phase_2b_start_time
                    rate = results_collected / elapsed if elapsed > 0 else 0
                    remaining = len(pending_tasks)
                    eta_seconds = remaining / rate if rate > 0 else 0
                    eta_str = f" | Rate: {rate:.1f}/s | ETA: {int(eta_seconds)}s" if rate > 0 else ""

                    file_changes_record.progress_message = progress_tracker.get_progress_message(
                        f'Phase 2b of 3: Collecting hash results',
                        results_collected,
                        len(task_results),
                        f'{len(changed_files)} changes found{eta_str}'
                    )
                    logger.info(f"Phase 2b heartbeat: {results_collected}/{len(task_results)} results collected, "
                              f"{len(pending_tasks)} pending, {len(changed_files)} changes found, "
                              f"batch_start_offset={batch_start_offset}")
                    db.session.commit()
                    last_heartbeat_time = current_time

                if self._is_cancelled_file_changes(file_changes_record):
                    logger.info("File changes check cancelled while collecting results")
                    break

                # Collect completed tasks from multiple batches in this iteration
                iteration_completed_tasks = []
                tasks_checked_count = 0

                # Check multiple batches per iteration for better coverage
                for batch_num in range(batches_per_iteration):
                    # Calculate batch boundaries with rotation
                    batch_start = (batch_start_offset + batch_num * check_batch_size) % len(pending_tasks)
                    batch_end = min(batch_start + check_batch_size, len(pending_tasks))

                    # Handle wrap-around at end of list
                    if batch_start >= len(pending_tasks):
                        break

                    batch_to_check = pending_tasks[batch_start:batch_end]
                    tasks_checked_count += len(batch_to_check)

                    # Check this batch for completed tasks
                    for i, task in enumerate(batch_to_check):
                        if task.ready():
                            try:
                                # Task is complete, get result immediately (no blocking)
                                result = task.get(timeout=1)
                                results_collected += 1

                                # Check if file changed
                                if result.get('changed'):
                                    change_info = {
                                        'file_path': result['file_path'],
                                        'change_type': result['change_type'],
                                        'stored_hash': result['stored_hash'],
                                        'current_hash': result['current_hash']
                                    }
                                    changed_files.append(change_info)
                                    self.changed_files_list.append(change_info)
                                    file_changes_record.changes_found = len(changed_files)

                                # Store task reference for removal (not index)
                                iteration_completed_tasks.append(task)

                                # Update progress every 100 results for more frequent UI updates
                                if results_collected % 100 == 0:
                                    file_changes_record.phase_current = results_collected
                                    file_changes_record.last_heartbeat = datetime.now(timezone.utc)
                                    file_changes_record.progress_message = progress_tracker.get_progress_message(
                                        f'Phase 2b of 3: Collecting hash results',
                                        results_collected,
                                        len(task_results),
                                        f'{len(changed_files)} changes found'
                                    )
                                    self._commit_with_retry(file_changes_record, results_collected)
                                    logger.info(f"Phase 2b: Collected {results_collected}/{len(task_results)} results, "
                                              f"{len(changed_files)} changes found, batch_start={batch_start}")

                                # Update in-memory state
                                with self.file_changes_lock:
                                    self.file_changes_state['files_processed'] = results_collected
                                    self.file_changes_state['changes_found'] = len(changed_files)

                            except Exception as e:
                                logger.error(f"Error getting task result: {e}")
                                results_collected += 1
                                iteration_completed_tasks.append(task)
                                # Continue collecting other results

                # Remove completed tasks efficiently using set-based filtering
                if iteration_completed_tasks:
                    completed_set = set(iteration_completed_tasks)
                    pending_tasks = [t for t in pending_tasks if t not in completed_set]
                    logger.debug(f"Removed {len(iteration_completed_tasks)} completed tasks, "
                                f"{len(pending_tasks)} remaining")

                # Rotate batch start offset for next iteration
                # IMPORTANT: Reset offset if it exceeds current list size (list shrinks as tasks complete)
                if pending_tasks:
                    batch_start_offset += batches_per_iteration * check_batch_size
                    # If offset exceeds current list size, wrap to beginning
                    if batch_start_offset >= len(pending_tasks):
                        batch_start_offset = 0

                # Only sleep if we found NO completed tasks after checking multiple batches
                if not iteration_completed_tasks and pending_tasks:
                    # No tasks ready in any of the batches checked, wait briefly
                    logger.debug(f"No ready tasks found after checking {tasks_checked_count} tasks, sleeping 100ms")
                    time.sleep(0.1)  # 100ms wait when no results ready

            logger.info(f"Phase 2b complete: Collected {results_collected} results, found {len(changed_files)} changed files")
            
            # Phase 3: Mark changed files for rescan (leverage parallel scanning)
            if changed_files and not self._is_cancelled_file_changes(file_changes_record):
                file_changes_record.phase = 'marking_for_rescan'
                file_changes_record.phase_number = 3
                file_changes_record.phase_total = len(changed_files)
                file_changes_record.phase_current = 0
                file_changes_record.progress_message = f'Phase 3 of 3: Marking {len(changed_files)} changed files as pending for rescan...'
                file_changes_record.last_heartbeat = datetime.now(timezone.utc)
                db.session.commit()

                logger.info(f"Starting Phase 3: Marking {len(changed_files)} changed files as pending for rescan")

                files_marked = 0
                last_heartbeat_time = time.time()

                for i, change_info in enumerate(changed_files):
                    # Heartbeat every 30 seconds
                    current_time = time.time()
                    if current_time - last_heartbeat_time >= 30:
                        file_changes_record.last_heartbeat = datetime.now(timezone.utc)
                        logger.info(f"Phase 3 heartbeat: {files_marked}/{len(changed_files)} files marked")
                        db.session.commit()
                        last_heartbeat_time = current_time

                    if self._is_cancelled_file_changes(file_changes_record):
                        break

                    file_changes_record.phase_current = i + 1

                    # Mark the file as pending for rescan by the regular scan workers
                    try:
                        file_record = ScanResult.query.filter_by(file_path=change_info['file_path']).first()
                        if file_record:
                            file_record.scan_status = 'pending'
                            files_marked += 1

                            # Log with change type details
                            if change_info['change_type'] == 'deleted':
                                logger.info(f"Marked deleted file for cleanup: {change_info['file_path']}")
                            elif change_info['change_type'] == 'modified':
                                logger.info(f"Marked modified file for rescan: {change_info['file_path']} (hash: {change_info['stored_hash'][:16]}... -> {change_info['current_hash'][:16]}...)")
                            else:
                                logger.info(f"Marked file for rescan: {change_info['file_path']} (type: {change_info['change_type']})")
                    except Exception as e:
                        logger.error(f"Error marking file for rescan {change_info['file_path']}: {e}")

                    # Commit every 10 files for real-time progress updates
                    if (i + 1) % 10 == 0:
                        file_changes_record.progress_message = f'Phase 3 of 3: Marked {files_marked}/{len(changed_files)} files as pending...'
                        db.session.commit()
                        logger.info(f"Phase 3: Marked {files_marked}/{len(changed_files)} files for rescan")

                # Final commit for remaining files
                file_changes_record.progress_message = f'Phase 3 of 3: Marked {files_marked}/{len(changed_files)} files as pending for rescan'
                db.session.commit()
                logger.info(f"Phase 3 complete: Marked {files_marked} files for rescan. They will be processed by parallel scan workers.")
            
            # Complete check
            if self._is_cancelled_file_changes(file_changes_record):
                file_changes_record.phase = 'cancelled'
                file_changes_record.progress_message = 'File changes check cancelled by user'
            else:
                file_changes_record.phase = 'complete'
                file_changes_record.progress_message = (
                    f'Check complete. Found {len(changed_files)} changed files, '
                    f'{file_changes_record.corrupted_found} newly corrupted.'
                )
            
            file_changes_record.is_active = False
            file_changes_record.end_time = datetime.now(timezone.utc)
            db.session.commit()
            
            # Create scan report for file changes operation
            # Always try to create a report even if there was an error, as long as we have some data
            if file_changes_record.phase in ('complete', 'error'):
                self._create_file_changes_report(file_changes_record, getattr(self, 'changed_files_list', []))
            
            with self.file_changes_lock:
                self.file_changes_state['is_running'] = False
                self.file_changes_state['phase'] = file_changes_record.phase
                
        except Exception as e:
            logger.error(f"Error during file changes check: {e}", exc_info=True)  # Add stack trace
            self._handle_file_changes_error(check_id, str(e))

            # Try to create error report
            try:
                file_changes_record = FileChangesState.query.filter_by(check_id=check_id).first()
                if file_changes_record:
                    self._create_file_changes_report(file_changes_record, getattr(self, 'changed_files_list', []))
            except Exception as report_error:
                logger.error(f"Failed to create error report: {report_error}")
    
    def _is_cancelled(self, cleanup_record: CleanupState) -> bool:
        """Check if cleanup has been cancelled"""
        try:
            # Force a fresh read from database
            db.session.expire(cleanup_record)
            db.session.refresh(cleanup_record)
            is_cancelled = getattr(cleanup_record, 'cancel_requested', False) or self.cleanup_state.get('cancel_requested', False)
            if is_cancelled:
                logger.info(f"Cleanup cancellation detected - DB: {cleanup_record.cancel_requested}, Memory: {self.cleanup_state.get('cancel_requested', False)}")
            return is_cancelled
        except Exception as e:
            logger.warning(f"Error checking cancel status from DB: {e}")
            return self.cleanup_state.get('cancel_requested', False)
    
    def _is_cancelled_file_changes(self, record: FileChangesState) -> bool:
        """Check if file changes check has been cancelled"""
        try:
            # Force a fresh read from database
            db.session.expire(record)
            db.session.refresh(record)
            is_cancelled = getattr(record, 'cancel_requested', False) or self.file_changes_state.get('cancel_requested', False)
            if is_cancelled:
                logger.info(f"File changes cancellation detected - DB: {record.cancel_requested}, Memory: {self.file_changes_state.get('cancel_requested', False)}")
            return is_cancelled
        except Exception as e:
            logger.warning(f"Error checking cancel status from DB: {e}")
            return self.file_changes_state.get('cancel_requested', False)

    def _commit_with_retry(self, record, context_info, max_retries=3, is_batch=False):
        """Commit database changes with retry logic for deadlock handling"""
        for attempt in range(max_retries):
            try:
                db.session.commit()
                if attempt > 0:
                    logger.info(f"Commit succeeded on attempt {attempt + 1} (context: {context_info})")
                return
            except Exception as e:
                error_msg = str(e).lower()
                is_deadlock = 'deadlock' in error_msg or 'lock timeout' in error_msg or 'could not obtain lock' in error_msg

                if is_deadlock and attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2  # Exponential backoff: 2s, 4s, 6s
                    logger.warning(f"Deadlock detected on attempt {attempt + 1}, retrying in {wait_time}s (context: {context_info}): {e}")

                    # Rollback and wait before retry
                    try:
                        db.session.rollback()
                        time.sleep(wait_time)
                        # Refresh the record to get latest state
                        db.session.expire(record)
                        db.session.refresh(record)
                    except Exception as refresh_error:
                        logger.error(f"Error during rollback/refresh: {refresh_error}")
                        if attempt == max_retries - 1:
                            raise
                else:
                    # Not a deadlock or final attempt failed
                    logger.error(f"Error committing {'batch' if is_batch else 'progress'} at {context_info} (attempt {attempt + 1}/{max_retries}): {e}")
                    try:
                        db.session.rollback()
                        # Refresh to avoid working with stale data
                        if record:
                            db.session.expire(record)
                            record = db.session.merge(record)
                    except Exception as rollback_error:
                        logger.error(f"Failed to rollback after commit error: {rollback_error}")

                    if attempt == max_retries - 1:
                        raise
    
    def _handle_cleanup_error(self, cleanup_id: int, error_msg: str):
        """Handle cleanup error"""
        try:
            cleanup_record = CleanupState.query.get(cleanup_id)
            if cleanup_record:
                cleanup_record.phase = 'error'
                cleanup_record.is_active = False
                cleanup_record.end_time = datetime.now(timezone.utc)
                cleanup_record.progress_message = f'Error: {error_msg}'
                db.session.commit()
        except:
            pass
        
        with self.cleanup_lock:
            self.cleanup_state['is_running'] = False
            self.cleanup_state['phase'] = 'error'
    
    def _handle_file_changes_error(self, check_id: str, error_msg: str):
        """Handle file changes check error"""
        try:
            record = FileChangesState.query.filter_by(check_id=check_id).first()
            if record:
                record.phase = 'error'
                record.is_active = False
                record.end_time = datetime.now(timezone.utc)
                record.progress_message = f'Error: {error_msg}'
                db.session.commit()
        except:
            pass
        
        with self.file_changes_lock:
            self.file_changes_state['is_running'] = False
            self.file_changes_state['phase'] = 'error'

    def _create_file_changes_report(self, file_changes_record: FileChangesState, changed_files_list=None):
        """Create a scan report for file changes operation

        Args:
            file_changes_record: The FileChangesState record
            changed_files_list: List of changed files with hash comparison details
        """
        try:
            # Calculate duration
            duration = None
            if file_changes_record.start_time and file_changes_record.end_time:
                duration = (file_changes_record.end_time - file_changes_record.start_time).total_seconds()

            # Create scan report
            report = ScanReport(
                scan_type='file_changes',
                start_time=file_changes_record.start_time,
                end_time=file_changes_record.end_time,
                duration_seconds=duration,
                total_files_discovered=file_changes_record.total_files,
                files_scanned=file_changes_record.files_processed,
                files_changed=file_changes_record.changes_found,
                files_corrupted_new=file_changes_record.corrupted_found,
                status='completed' if file_changes_record.phase == 'complete' else file_changes_record.phase,
                error_message=file_changes_record.error_message
            )

            # Store the list of changed files with hash comparison details in directories_scanned field as JSON
            # This field is repurposed for file changes reports to store the changed files list with hash info
            if changed_files_list:
                import json
                report.directories_scanned = json.dumps(changed_files_list)

            db.session.add(report)
            db.session.commit()

            logger.info(f"Created file changes report {report.report_id} with {len(changed_files_list) if changed_files_list else 0} changed files")

        except Exception as e:
            logger.error(f"Failed to create file changes report: {e}")
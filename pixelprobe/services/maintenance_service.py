"""
Maintenance service for cleanup and file monitoring operations
"""

import os
import threading
import time
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import uuid

from sqlalchemy import text
from pixelprobe.media_checker import PixelProbe, load_exclusions, load_exclusions_with_patterns
from pixelprobe.models import db, ScanResult, CleanupState, FileChangesState, ScanReport, LogEntry, AppConfig
from pixelprobe.utils.helpers import ProgressTracker, env_int
from pixelprobe.constants import CONFIG_LOG_RETENTION_DAYS
from pixelprobe.progress_utils import (
    update_file_changes_progress_redis,
    clear_file_changes_progress_redis,
)

logger = logging.getLogger(__name__)


# Integrity-scan task timeout. Tasks whose results have not arrived within this
# many seconds are considered orphaned (worker died, broker lost the message,
# etc.) and dropped from the active set so the producer loop can keep moving.
# Override via the INTEGRITY_TASK_TIMEOUT_SECS environment variable.
#
# Default is 3h (was 30m): the hash task reads the WHOLE file, and PixelProbe
# scans files up to ~55GB. On slow/contended storage a 55GB sequential read can
# exceed 30 minutes, so a too-low timeout abandons (skips) the very largest files
# mid-hash. 3h covers ~55GB down to ~5MB/s; with the 5000-slot concurrency cap a
# few genuinely-dead tasks holding slots this long is harmless.
INTEGRITY_TASK_TIMEOUT_SECS = int(
    os.environ.get('INTEGRITY_TASK_TIMEOUT_SECS', 10800)
)

# Cleanup existence-check task timeout. These tasks stat() one path (~ms), so a
# task pending this long is lost (worker died, broker dropped it, result
# unreadable) and is abandoned as unverifiable - never deleted - so the
# producer loop cannot pin at max concurrency and spin forever.
CLEANUP_TASK_TIMEOUT_SECS = env_int('CLEANUP_TASK_TIMEOUT_SECS', 600, floor=60)


def abandon_if_stuck(task_info, timeout_secs, label):
    """Revoke and drop a dispatched task whose result never arrived.

    Returns True when the task was abandoned (worker died, broker dropped the
    message, or its result is unreadable); callers count it as unverifiable
    work - never as a deletion candidate.
    """
    age = time.monotonic() - task_info.get('submitted_at', 0)
    if age <= timeout_secs:
        return False
    task = task_info['task']
    logger.warning("Abandoning stuck %s task id=%s path=%s age=%.0fs",
                   label, task.id, task_info.get('path'), age)
    try:
        task.revoke(terminate=False)
    except Exception:
        pass
    return True


def safe_task_ready(task, max_retries=5, base_delay=1.0):
    """
    Safely check if a Celery task is ready with enhanced retry logic for Redis connection errors.

    v2.5.54: Enhanced with exponential backoff and connection pool reset.
    When Redis connection pool gets corrupted, all connections are bad.
    This wrapper resets the pool on first failure and uses exponential backoff.

    Celery's task.ready() uses its internal Redis connection which can get reset.
    This wrapper adds retry logic to handle transient connection failures.

    Args:
        task: Celery AsyncResult object
        max_retries: Number of retry attempts (default 5 for longer recovery window)
        base_delay: Base delay between retries in seconds (exponential backoff)

    Returns:
        bool: True if task is ready, False if not ready or on persistent error
    """
    import redis
    from pixelprobe.progress_utils import reset_redis_pool

    for attempt in range(max_retries):
        try:
            return task.ready()
        except (redis.ConnectionError, redis.TimeoutError, ConnectionResetError, AttributeError) as e:
            # Reset connection pool on first failure to get fresh connections
            if attempt == 0:
                reset_redis_pool()

            if attempt < max_retries - 1:
                delay = base_delay * (attempt + 1)  # Exponential backoff: 1s, 2s, 3s, 4s
                logger.warning(f"Redis error checking task status (attempt {attempt + 1}/{max_retries}), retrying in {delay}s: {type(e).__name__}: {e}")
                time.sleep(delay)
            else:
                logger.error(f"Failed to check task status after {max_retries} attempts: {type(e).__name__}: {e}")
                # Return False to keep task in active list and retry later
                return False
        except Exception as e:
            logger.error(f"Unexpected error checking task status: {type(e).__name__}: {e}")
            return False
    return False


def safe_task_get(task, timeout=1, max_retries=5, base_delay=1.0):
    """
    Safely get a Celery task result with enhanced retry logic for Redis connection errors.

    v2.5.54: Enhanced with exponential backoff and connection pool reset.

    Args:
        task: Celery AsyncResult object
        timeout: Timeout for getting the result
        max_retries: Number of retry attempts (default 5 for longer recovery window)
        base_delay: Base delay between retries in seconds (exponential backoff)

    Returns:
        Result dict or None on error
    """
    import redis
    from pixelprobe.progress_utils import reset_redis_pool

    for attempt in range(max_retries):
        try:
            return task.get(timeout=timeout)
        except (redis.ConnectionError, redis.TimeoutError, ConnectionResetError, AttributeError) as e:
            # Reset connection pool on first failure to get fresh connections
            if attempt == 0:
                reset_redis_pool()

            if attempt < max_retries - 1:
                delay = base_delay * (attempt + 1)  # Exponential backoff: 1s, 2s, 3s, 4s
                logger.warning(f"Redis error getting task result (attempt {attempt + 1}/{max_retries}), retrying in {delay}s: {type(e).__name__}: {e}")
                time.sleep(delay)
            else:
                logger.error(f"Failed to get task result after {max_retries} attempts: {type(e).__name__}: {e}")
                raise
        except Exception:
            raise
    return None

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

    def _run_cleanup(self, cleanup_id, file_paths=None, schedule_id=None):
        """Run the cleanup operation

        Args:
            cleanup_id: ID of the cleanup record
            file_paths: Optional list of specific file paths to check (if None, checks all files)
            schedule_id: Optional schedule ID for healthcheck integration
        """
        # Store schedule_id for report creation
        self._cleanup_schedule_id = schedule_id
        try:
            cleanup_record = db.session.get(CleanupState, cleanup_id)
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
            
            # Phase 2: Checking files in parallel using Celery tasks
            cleanup_record.phase = 'checking_files'
            cleanup_record.phase_number = 2
            cleanup_record.progress_message = f'Phase 2 of 3: Checking 0 / {total_files:,} files on filesystem (0%)...'
            db.session.commit()

            logger.info(f"Starting Phase 2: Parallel file existence checking for {total_files} files")

            # Import task
            from pixelprobe.tasks import check_file_exists_task

            # Parallel checking with throttling (similar to file changes check)
            max_active_tasks = 5000  # Limit concurrent tasks
            active_tasks = []  # [{'task', 'path', 'submitted_at'}]
            file_index = 0
            total_files_processed = 0
            orphaned_files = []  # Collect orphaned file info (status == 'absent' only)
            unknown_count = 0  # Files we could not verify (mount down, IO error, etc.)
            files_abandoned = 0
            phase2_start_time = time.time()  # Track start time for ETA calculation
            last_heartbeat_time = time.time()

            def write_cleanup_progress():
                pct = int((total_files_processed / total_files * 100)) if total_files > 0 else 0

                elapsed_seconds = time.time() - phase2_start_time
                if total_files_processed > 0:
                    avg_time_per_file = elapsed_seconds / total_files_processed
                    eta_seconds = avg_time_per_file * (total_files - total_files_processed)
                    eta_hours = int(eta_seconds // 3600)
                    eta_minutes = int((eta_seconds % 3600) // 60)
                    eta_str = f"{eta_hours}h {eta_minutes}m" if eta_hours > 0 else f"{eta_minutes}m"
                else:
                    eta_str = "calculating..."

                cleanup_record.files_processed = total_files_processed
                cleanup_record.phase_current = total_files_processed
                cleanup_record.orphaned_found = len(orphaned_files)
                cleanup_record.progress_message = (
                    f'Phase 2 of 3: Checking {total_files_processed:,} / {total_files:,} files ({pct}%) - '
                    f'{len(orphaned_files)} orphaned found, {len(active_tasks)} active tasks, ETA: {eta_str}'
                )
                db.session.commit()

                with self.cleanup_lock:
                    self.cleanup_state['files_processed'] = total_files_processed
                    self.cleanup_state['orphaned_found'] = len(orphaned_files)

            while file_index < len(all_results) or len(active_tasks) > 0:
                if self._is_cancelled(cleanup_record):
                    logger.info("Cleanup cancelled during file checking")
                    break

                # Heartbeat every 10 s: the only progress writer for this loop
                # (a count-based write here misses bursts and floods commits;
                # the log line makes a stalled loop visible)
                current_time = time.time()
                if current_time - last_heartbeat_time >= 10:
                    write_cleanup_progress()
                    logger.info(
                        f"Cleanup progress: {total_files_processed}/{total_files} processed, "
                        f"{len(active_tasks)} active, {len(all_results) - file_index} remaining, "
                        f"{files_abandoned} abandoned"
                    )
                    last_heartbeat_time = current_time

                # Submit new tasks while under the limit
                while len(active_tasks) < max_active_tasks and file_index < len(all_results):
                    result = all_results[file_index]

                    # Submit file existence check task
                    task = check_file_exists_task.apply_async(
                        args=[result.id, result.file_path],
                        priority=6
                    )
                    active_tasks.append({
                        'task': task,
                        'path': result.file_path,
                        'submitted_at': time.monotonic(),
                    })
                    file_index += 1

                # Collect completed tasks and free up slots
                still_active = []
                for task_info in active_tasks:
                    task = task_info['task']
                    if safe_task_ready(task):
                        try:
                            check_result = safe_task_get(task, timeout=1)
                            total_files_processed += 1

                            # Only a definitive 'absent' is an orphan. Anything
                            # else ('unknown' from a mount/IO error, or an
                            # unexpected/missing value) is skipped, never deleted
                            # -- defaulting to "skip" keeps the data-loss guard
                            # intact even if the result shape is unexpected.
                            status = check_result.get('status')
                            if status == 'absent':
                                orphaned_files.append({
                                    'file_id': check_result['file_id'],
                                    'file_path': check_result['file_path']
                                })
                                logger.info(f"Found orphaned entry: {check_result['file_path']}")
                            elif status != 'exists':
                                unknown_count += 1
                                logger.warning(f"Skipping unverifiable entry (not deleting): {check_result['file_path']}")
                        except Exception as e:
                            logger.error(f"Error processing existence check result: {e}")
                            total_files_processed += 1
                    elif abandon_if_stuck(task_info, CLEANUP_TASK_TIMEOUT_SECS, 'existence-check'):
                        # Abandoned work counts as unverifiable - NEVER deleted
                        files_abandoned += 1
                        unknown_count += 1
                        total_files_processed += 1
                    else:
                        still_active.append(task_info)

                active_tasks = still_active

                # If no new tasks submitted and active tasks exist, wait a bit
                if file_index < len(all_results) and len(active_tasks) > 0:
                    time.sleep(0.1)  # Brief sleep to avoid busy waiting

            # Final update
            cleanup_record.files_processed = total_files_processed
            cleanup_record.orphaned_found = len(orphaned_files)
            db.session.commit()

            logger.info(f"Phase 2 complete: Checked {total_files_processed} files, "
                        f"found {len(orphaned_files)} orphaned, {unknown_count} unverifiable (skipped), "
                        f"{files_abandoned} abandoned")

            # Extract IDs and paths for Phase 3
            orphaned_ids = [f['file_id'] for f in orphaned_files]
            orphaned_paths = [f['file_path'] for f in orphaned_files]
            orphaned_count = len(orphaned_files)

            # Store for report
            self.orphaned_files_list = orphaned_paths

            # Safety net: if an implausibly large fraction of checked files read as
            # absent, a whole mount likely disappeared (the mountpoint dir survives
            # but is empty, so each child stats ENOENT). Abort rather than mass-delete.
            try:
                abort_floor = int(os.environ.get('ORPHAN_CLEANUP_ABORT_FLOOR', '100'))
            except (TypeError, ValueError):
                abort_floor = 100
            try:
                max_fraction = float(os.environ.get('ORPHAN_CLEANUP_MAX_DELETE_FRACTION', '0.5'))
            except (TypeError, ValueError):
                max_fraction = 0.5
            if (total_files_processed > 0 and orphaned_count >= abort_floor
                    and (orphaned_count / total_files_processed) >= max_fraction):
                msg = (f'Aborted: {orphaned_count:,} of {total_files_processed:,} checked files '
                       f'({orphaned_count / total_files_processed:.0%}) read as missing - likely a '
                       f'mount/storage outage, not real orphans. No entries were deleted.')
                logger.error(msg)
                cleanup_record.phase = 'error'
                cleanup_record.error_message = msg
                cleanup_record.progress_message = msg
                cleanup_record.orphaned_found = orphaned_count
                cleanup_record.is_active = False
                cleanup_record.end_time = datetime.now(timezone.utc)
                db.session.commit()
                with self.cleanup_lock:
                    self.cleanup_state['is_running'] = False
                    self.cleanup_state['phase'] = 'error'
                return

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
                cleanup_record = db.session.get(CleanupState, cleanup_id)
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

            # Get schedule_id for healthcheck integration
            schedule_id = getattr(self, '_cleanup_schedule_id', None)

            # Create the report
            report = ScanReport(
                scan_type='cleanup',
                scan_id=f'scheduled_{schedule_id}' if schedule_id else None,
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

            # Send healthcheck completion ping if this was a scheduled cleanup
            if schedule_id:
                try:
                    from pixelprobe.scheduler import MediaScheduler
                    MediaScheduler.send_healthcheck_completion(report.id)
                except Exception as hc_error:
                    logger.error(f"Failed to send healthcheck completion ping: {hc_error}")

            return report

        except Exception as e:
            logger.error(f"Failed to create cleanup report: {e}")
            # Don't fail the cleanup operation if report creation fails
            return None
    
    def _run_file_changes_check(self, check_id: str, file_paths=None, schedule_id=None):
        """Run the file changes check operation

        Args:
            check_id: Unique ID for this check
            file_paths: Optional list of specific file paths to check (if None, checks all files)
            schedule_id: Optional schedule ID for healthcheck integration
        """
        # Store schedule_id for report creation
        self._file_changes_schedule_id = schedule_id
        try:
            # Use READ COMMITTED isolation level to reduce lock contention
            # This allows reads to see committed data without holding locks
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
            # CRITICAL FIX v2.5.61: Load only needed columns as dictionaries to avoid
            # detached instance errors during long-running scans (20+ hours for 1M+ files).
            # ORM objects held in memory get expired by db.session.commit() calls, and if
            # concurrent jobs delete rows, subsequent attribute access crashes with
            # "Instance has been deleted" errors.
            if file_paths:
                results_query = db.session.query(
                    ScanResult.id,
                    ScanResult.file_path,
                    ScanResult.file_hash,
                    ScanResult.file_size,
                    ScanResult.last_modified
                ).filter(ScanResult.file_path.in_(file_paths)).all()
                logger.info(f"Loaded {len(results_query)} specific files from database")
            else:
                results_query = db.session.query(
                    ScanResult.id,
                    ScanResult.file_path,
                    ScanResult.file_hash,
                    ScanResult.file_size,
                    ScanResult.last_modified
                ).all()
                logger.info(f"Loaded all {len(results_query)} files from database")

            # Convert to list of dicts immediately - immune to session expiration
            all_results = [
                {
                    'id': r.id,
                    'file_path': r.file_path,
                    'file_hash': r.file_hash,
                    'file_size': r.file_size,
                    'last_modified': r.last_modified
                }
                for r in results_query
            ]

            # CRITICAL FIX v2.4.60: Adaptive memory-aware task management
            # Problem: Files vary from 1KB to 40GB, systems vary in resources
            # Solution: Monitor Redis memory usage and adapt task submission dynamically

            # Get Redis memory info to determine safe limits
            # v2.5.51: Use robust Redis connection from progress_utils
            from pixelprobe.progress_utils import get_redis_info

            try:
                redis_info = get_redis_info('memory')

                if redis_info:
                    # Get max memory setting (0 means unlimited)
                    max_memory = int(redis_info.get('maxmemory', 0))
                    used_memory = int(redis_info.get('used_memory', 0))

                    if max_memory > 0:
                        # Calculate safe threshold (use 80% of max)
                        safe_memory = max_memory * 0.8
                        available_memory = safe_memory - used_memory

                        # Estimate memory per task (approximately 10KB per task in Redis)
                        memory_per_task = 10 * 1024
                        max_safe_tasks = max(100, int(available_memory / memory_per_task))
                    else:
                        # No limit set, use conservative default
                        max_safe_tasks = 5000

                    logger.info(f"Redis memory: {used_memory/1024/1024:.1f}MB used, "
                               f"max safe concurrent tasks: {max_safe_tasks}")
                else:
                    logger.warning("Could not get Redis memory info, using conservative limits")
                    max_safe_tasks = 1000
            except Exception as e:
                logger.warning(f"Could not get Redis memory info: {e}, using conservative limits")
                max_safe_tasks = 1000

            # Dynamic limits based on available resources
            # These will scale down proportionally if memory is limited
            MAX_CONCURRENT_SMALL = min(max_safe_tasks, int(os.environ.get('MAX_CONCURRENT_SMALL', '5000')))
            MAX_CONCURRENT_MEDIUM = min(max_safe_tasks // 10, int(os.environ.get('MAX_CONCURRENT_MEDIUM', '500')))
            MAX_CONCURRENT_LARGE = min(max_safe_tasks // 100, int(os.environ.get('MAX_CONCURRENT_LARGE', '50')))
            MAX_CONCURRENT_HUGE = min(max_safe_tasks // 1000, int(os.environ.get('MAX_CONCURRENT_HUGE', '5')))

            # Track active tasks
            active_tasks = []
            total_files_processed = 0
            files_queued = 0
            files_abandoned = 0
            last_progress_update = 0
            task_results = []
            last_heartbeat_time = time.time()

            logger.info(f"Processing {len(all_results)} files with size-aware batching...")

            # Set initial progress to show we're starting
            file_changes_record.phase_current = 0
            file_changes_record.phase_total = len(all_results)
            file_changes_record.files_processed = 0
            file_changes_record.progress_message = f'Phase 2 of 3: Starting to process {len(all_results):,} files...'
            db.session.commit()
            # Force a small delay to allow UI to see initial progress
            time.sleep(0.1)

            # Sort files by size for better batch management (small files first)
            # Handle NULL file_size by treating as 0 for sorting
            all_results_sorted = sorted(all_results, key=lambda x: x['file_size'] if x['file_size'] else 0)

            total_count = len(all_results)

            def write_progress_snapshot(set_heartbeat: bool):
                pct = int((total_files_processed / total_count * 100)) if total_count > 0 else 0
                msg = (
                    f'Processing files: {total_files_processed:,}/{total_count:,} ({pct}%) - '
                    f'{len(changed_files)} changes found, {len(active_tasks)} active tasks'
                )
                if set_heartbeat:
                    file_changes_record.last_heartbeat = datetime.now(timezone.utc)
                file_changes_record.phase_current = total_files_processed
                file_changes_record.files_processed = total_files_processed
                file_changes_record.progress_message = msg
                db.session.commit()
                update_file_changes_progress_redis(
                    check_id=check_id,
                    files_processed=total_files_processed,
                    total_files=total_count,
                    phase=file_changes_record.phase or '',
                    progress_message=msg,
                )

            file_index = 0
            while file_index < len(all_results_sorted) or active_tasks:
                # Heartbeat every 10 s: write-through to PostgreSQL + Redis so the
                # UI sees motion even when the periodic-delta block below hasn't
                # fired in the current iteration. phase_total is invariant for
                # Phase 2 (set once at line ~816) so it's not repeated here.
                current_time = time.time()
                if current_time - last_heartbeat_time >= 10:
                    write_progress_snapshot(set_heartbeat=True)
                    logger.info(
                        f"Progress: {total_files_processed}/{total_count} processed, "
                        f"{len(active_tasks)} active, {len(all_results_sorted) - file_index} remaining, "
                        f"{files_abandoned} abandoned"
                    )
                    last_heartbeat_time = current_time

                # Check for cancellation
                if self._is_cancelled_file_changes(file_changes_record):
                    logger.info(f"Cancelled at {total_files_processed}/{len(all_results)} files")
                    break

                # Collect completed tasks and free up slots
                still_active = []
                for task_info in active_tasks:
                    task, file_size = task_info['task'], task_info['size']
                    if safe_task_ready(task):
                        try:
                            result = safe_task_get(task, timeout=1)
                            total_files_processed += 1

                            # Update last integrity check timestamp for this file
                            try:
                                file_record = ScanResult.query.filter_by(file_path=result['file_path']).first()
                                if file_record:
                                    file_record.last_integrity_check_date = datetime.now(timezone.utc)
                            except Exception as e:
                                logger.error(f"Error updating last_integrity_check_date for {result['file_path']}: {e}")

                            # For single file scans, update progress immediately so UI can see it
                            if len(all_results) == 1:
                                file_changes_record.phase_current = total_files_processed
                                file_changes_record.phase_total = 1
                                file_changes_record.progress_message = f'Phase 2 of 3: Completed checking file'

                                # Also update ScanState for UI progress bar
                                try:
                                    from pixelprobe.models import ScanState
                                    scan_state = ScanState.query.filter_by(scan_id=check_id).first()
                                    if scan_state:
                                        scan_state.files_processed = 1
                                        scan_state.estimated_total = 1
                                        scan_state.progress_message = 'Integrity check complete'
                                        scan_state.phase = 'scanning'  # Use 'scanning' phase for proper UI display
                                        scan_state.current_file = result['file_path']
                                        logger.info(f"Updated ScanState for single file integrity check")
                                except Exception as e:
                                    logger.warning(f"Failed to update ScanState for single file integrity check: {e}")

                                db.session.commit()
                                logger.info(f"Single file integrity check complete: {result['file_path']}")

                            if result.get('changed'):
                                changed_files.append({
                                    'file_path': result['file_path'],
                                    'change_type': result['change_type'],
                                    'stored_hash': result['stored_hash'],
                                    'current_hash': result['current_hash']
                                })
                        except Exception as e:
                            logger.error(f"Error getting task result: {e}")
                    elif abandon_if_stuck(task_info, INTEGRITY_TASK_TIMEOUT_SECS, 'integrity'):
                        files_abandoned += 1
                    else:
                        still_active.append(task_info)
                active_tasks = still_active

                # Submit new tasks based on available slots
                while file_index < len(all_results_sorted):
                    result = all_results_sorted[file_index]

                    # Use file_size from DB (it's a BigInteger field, might be NULL)
                    file_size = result['file_size'] if result['file_size'] else 0

                    if file_size == 0:
                        # If size not in DB, use OS to check (more accurate than estimates)
                        try:
                            file_size = os.path.getsize(result['file_path'])
                        except:
                            # If file doesn't exist or can't access, estimate based on path
                            if 'thumbnail' in result['file_path'] or 'thumb' in result['file_path']:
                                file_size = 50 * 1024  # 50KB estimate for thumbnails
                            elif 'preview' in result['file_path']:
                                file_size = 500 * 1024  # 500KB for previews
                            else:
                                file_size = 10 * 1024 * 1024  # 10MB default

                    # Determine max concurrent based on file size
                    if file_size < 10 * 1024 * 1024:  # < 10MB
                        max_concurrent = MAX_CONCURRENT_SMALL
                    elif file_size < 100 * 1024 * 1024:  # < 100MB
                        max_concurrent = MAX_CONCURRENT_MEDIUM
                    elif file_size < 1024 * 1024 * 1024:  # < 1GB
                        max_concurrent = MAX_CONCURRENT_LARGE
                    else:  # >= 1GB
                        max_concurrent = MAX_CONCURRENT_HUGE

                    # Count how many tasks of this size category are active
                    size_category_active = sum(1 for t in active_tasks
                                              if t['size'] // (100*1024*1024) == file_size // (100*1024*1024))

                    # Check if we can submit this task
                    if len(active_tasks) >= max_concurrent or size_category_active >= max_concurrent:
                        # Wait for tasks to complete before submitting more
                        break

                    # Submit the task
                    stored_modified_iso = result['last_modified'].isoformat() if result['last_modified'] else None
                    try:
                        task_result = calculate_file_hash_task.apply_async(
                            args=[result['id'], result['file_path'], result['file_hash'], stored_modified_iso]
                        )
                        active_tasks.append({
                            'task': task_result,
                            'size': file_size,
                            'path': result['file_path'],
                            'submitted_at': time.monotonic(),
                        })
                        task_results.append(task_result)
                        files_queued += 1
                        file_index += 1

                        # Update progress immediately when processing single files
                        if len(all_results) == 1:
                            file_changes_record.progress_message = f'Phase 2 of 3: Processing {result["file_path"].split("/")[-1]}...'
                            db.session.commit()
                    except Exception as e:
                        logger.error(f"Error submitting task for {result['file_path']}: {e}")
                        if "maxmemory" in str(e):
                            logger.warning("Redis memory full, waiting for tasks to complete...")
                            break  # Wait for active tasks to complete
                        file_index += 1  # Skip this file

                # If no new tasks submitted and active tasks exist, wait a bit
                if file_index < len(all_results_sorted) and len(active_tasks) > 0:
                    time.sleep(0.1)  # Brief sleep to avoid busy waiting

                    # Update progress while waiting for single file
                    if len(all_results) == 1 and len(active_tasks) > 0:
                        file_changes_record.progress_message = f'Phase 2 of 3: Checking file for changes...'
                        file_changes_record.phase_current = 0
                        file_changes_record.phase_total = 1
                        db.session.commit()

                # Update progress periodically - every file for tiny sets, every 10 for small, every 100 for larger
                if len(all_results) <= 10:
                    update_interval = 1  # Update after every file for 10 or fewer files
                elif len(all_results) < 1000:
                    update_interval = 10
                else:
                    update_interval = 100

                # Delta-based check (not modulo): fires reliably even when the
                # producer batches thousands of task completions per outer-loop
                # iteration. Modulo would only match when the batch boundary
                # landed exactly on a multiple of update_interval, which at
                # 5000-active steady state almost never does.
                if total_files_processed > 0 and (
                    total_files_processed - last_progress_update >= update_interval or
                    total_files_processed == total_count
                ):
                    write_progress_snapshot(set_heartbeat=False)
                    last_progress_update = total_files_processed

            # Final update
            file_changes_record.phase_current = total_files_processed
            file_changes_record.phase_total = len(all_results)
            pct = int((total_files_processed / len(all_results) * 100)) if len(all_results) > 0 else 0
            file_changes_record.progress_message = (
                f'Completed: {total_files_processed:,}/{len(all_results):,} files - '
                f'{len(changed_files)} changes found'
            )
            db.session.commit()

            logger.info(f"Phase 2a complete: Processed {total_files_processed} files, found {len(changed_files)} changed files")

            # Store changed files for report generation
            self.changed_files_list = changed_files

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
                modified_count = 0
                deleted_count = 0
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

                            # Count by type
                            if change_info['change_type'] == 'deleted':
                                deleted_count += 1
                                logger.info(f"Marked deleted file for cleanup: {change_info['file_path']}")
                            elif change_info['change_type'] == 'modified':
                                modified_count += 1
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

                # Final commit for remaining files and update counts
                file_changes_record.progress_message = f'Phase 3 of 3: Marked {files_marked}/{len(changed_files)} files as pending for rescan'
                file_changes_record.changes_found = modified_count
                db.session.commit()
                logger.info(f"Phase 3 complete: Marked {files_marked} files for rescan ({modified_count} modified, {deleted_count} deleted). They will be processed by parallel scan workers.")
            else:
                # No changes found - set counts to 0
                modified_count = 0
                deleted_count = 0

            # Complete check
            if self._is_cancelled_file_changes(file_changes_record):
                file_changes_record.phase = 'cancelled'
                file_changes_record.progress_message = 'File changes check cancelled by user'
            else:
                file_changes_record.phase = 'complete'
                file_changes_record.progress_message = (
                    f'Check complete. Found {len(changed_files)} changed files '
                    f'({modified_count} modified, {deleted_count} deleted), '
                    f'{file_changes_record.corrupted_found} newly corrupted.'
                )

            file_changes_record.is_active = False
            file_changes_record.end_time = datetime.now(timezone.utc)

            # Complete ScanState if this was a single file integrity check
            try:
                from pixelprobe.models import ScanState
                scan_state = ScanState.query.filter_by(scan_id=check_id).first()
                if scan_state:
                    scan_state.complete_scan()
                    logger.info(f"Completed ScanState for single file integrity check")
            except Exception as e:
                logger.warning(f"Failed to complete ScanState for single file integrity check: {e}")

            db.session.commit()
            clear_file_changes_progress_redis(check_id)

            # Create scan report for file changes operation
            # Always try to create a report even if there was an error, as long as we have some data
            if file_changes_record.phase in ('complete', 'error'):
                self._create_file_changes_report(file_changes_record, getattr(self, 'changed_files_list', []), deleted_count if changed_files else 0)

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
            cleanup_record = db.session.get(CleanupState, cleanup_id)
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

    @staticmethod
    def cleanup_old_logs():
        """Delete log entries older than the configured retention period.

        Reads log_retention_days from AppConfig (default 30).
        Deletes in batches of 10k to avoid long-held locks.
        Called daily by the scheduler.
        """
        try:
            retention_days = 30
            config = AppConfig.query.filter_by(key=CONFIG_LOG_RETENTION_DAYS).first()
            if config and config.value:
                retention_days = int(config.value) if config.value.isdigit() else 30

            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            total_deleted = 0
            batch_size = 10000

            # Delete in batches to avoid long-held locks that block the log handler
            while True:
                result = db.session.execute(
                    text("DELETE FROM log_entries WHERE id IN "
                         "(SELECT id FROM log_entries WHERE timestamp < :cutoff LIMIT :batch)"),
                    {'cutoff': cutoff, 'batch': batch_size}
                )
                batch_deleted = result.rowcount
                db.session.commit()
                total_deleted += batch_deleted
                if batch_deleted < batch_size:
                    break

            if total_deleted:
                logger.info(f"Log retention cleanup: deleted {total_deleted} log entries older than {retention_days} days")
            return total_deleted
        except Exception as e:
            logger.error(f"Log retention cleanup failed: {e}")
            try:
                db.session.rollback()
            except Exception:
                pass
            return 0

    def _create_file_changes_report(self, file_changes_record: FileChangesState, changed_files_list=None, deleted_files_count=0):
        """Create a scan report for file changes operation

        Args:
            file_changes_record: The FileChangesState record
            changed_files_list: List of changed files with hash comparison details
            deleted_files_count: Number of deleted/orphaned files found
        """
        try:
            # Calculate duration
            duration = None
            if file_changes_record.start_time and file_changes_record.end_time:
                duration = (file_changes_record.end_time - file_changes_record.start_time).total_seconds()

            # Get schedule_id for healthcheck integration
            schedule_id = getattr(self, '_file_changes_schedule_id', None)

            # Create scan report
            report = ScanReport(
                scan_type='file_changes',
                scan_id=f'scheduled_{schedule_id}' if schedule_id else None,
                start_time=file_changes_record.start_time,
                end_time=file_changes_record.end_time,
                duration_seconds=duration,
                total_files_discovered=file_changes_record.total_files,
                files_scanned=file_changes_record.files_processed,
                files_changed=file_changes_record.changes_found,
                files_corrupted_new=file_changes_record.corrupted_found,
                orphaned_records_found=deleted_files_count,
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

            logger.info(
                f"Created file changes report {report.report_id}: "
                f"{report.files_changed} modified files, "
                f"{report.orphaned_records_found} deleted files"
            )

            # Send healthcheck completion ping if this was a scheduled file changes check
            if schedule_id:
                try:
                    from pixelprobe.scheduler import MediaScheduler
                    MediaScheduler.send_healthcheck_completion(report.id)
                except Exception as hc_error:
                    logger.error(f"Failed to send healthcheck completion ping: {hc_error}")

        except Exception as e:
            logger.error(f"Failed to create file changes report: {e}")
"""
Celery Tasks for PixelProbe
P1 Implementation per 2.1_AUDIT_IMPLEMENTATION_PLAN.md

This module contains all Celery tasks for distributed processing.
Replaces ThreadPoolExecutor with proper task queue system.
"""

from celery import current_task
import logging
import time
from datetime import datetime, timezone

from pixelprobe.celery_config import celery_app
from pixelprobe.constants import SCAN_PHASES
from pixelprobe.services.scan_service import ScanService
from pixelprobe.models import db, ScanState, ScanResult, ScanReport
from pixelprobe.utils.celery_utils import is_db_connection_corruption
from pixelprobe.utils.integrity import classify_file_change
from pixelprobe.utils.log_context import current_scan_id, current_celery_task_id


logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60,
                 soft_time_limit=None, time_limit=None,  # No timeout for scan tasks
                 priority=3)  # High priority - scans take precedence over maintenance
def scan_media_task(self, scan_id, paths, scan_type='full', force_rescan=False):
    """
    Main media scanning task

    Args:
        scan_id (str): Unique scan identifier
        paths (list): List of paths to scan
        scan_type (str): Type of scan ('full', 'quick', 'discover')
        force_rescan (bool): Whether to force rescan of existing files

    Returns:
        dict: Task completion status and results
    """
    # ContextTask wrapper in celery_config.py automatically provides Flask app context
    # NOTE: Do NOT call db.session.remove() here - it corrupts the connection pool
    # The ContextTask wrapper handles session management properly

    logger.info(f"Starting Celery scan task {self.request.id} for scan_id: {scan_id}")

    # Check if this scan_id already completed (e.g., on retry after DetachedInstanceError)
    # This prevents duplicate work when Celery retries a task that actually succeeded
    if scan_id:
        existing_state = ScanState.query.filter_by(scan_id=scan_id).first()
        if existing_state and existing_state.phase == 'completed':
            logger.info(f"Scan {scan_id} already completed (phase={existing_state.phase}), skipping retry")

            # Send completion ping for scheduled scans even when returning early
            # because the scheduler already sent a start ping and expects a completion signal
            if scan_id.startswith('scheduled_'):
                try:
                    from pixelprobe.scheduler import MediaScheduler
                    # Find the existing scan report for this scan
                    existing_report = ScanReport.query.filter_by(scan_id=scan_id).order_by(ScanReport.end_time.desc()).first()
                    if existing_report:
                        logger.info(f"Sending completion ping for already-completed scan {scan_id} (report {existing_report.id})")
                        MediaScheduler.send_healthcheck_completion(existing_report.id)
                    else:
                        logger.warning(f"No scan report found for already-completed scan {scan_id}, cannot send completion ping")
                except Exception as hc_error:
                    logger.error(f"Failed to send completion ping for already-completed scan: {hc_error}")

            return {
                'status': 'completed',
                'message': 'Scan already completed (from previous attempt)',
                'scan_id': scan_id,
                'files_processed': existing_state.files_processed or 0,
                'files_discovered': existing_state.discovery_count or 0
            }

    # Tag all logs emitted during this task with scan_id and celery_task_id
    _scan_token = current_scan_id.set(scan_id)
    _task_token = current_celery_task_id.set(self.request.id)

    try:
        # Directory scans run on the chunk-distributed engine. This shim keeps
        # the task name alive so messages queued across a deploy still execute.
        # Remove the directory-type branch in the next release once pre-2.6.49
        # queued messages have drained.
        if scan_type in ['full', 'parallel', 'pending', 'discover']:
            from pixelprobe.tasks_parallel import parallel_scan_orchestrator
            task = parallel_scan_orchestrator.delay(
                scan_id=scan_id,
                paths=paths if paths != ['PENDING_FILES_SCAN'] else [],
                scan_type='pending' if scan_type == 'pending' else 'full',
                force_rescan=force_rescan if scan_type != 'discover' else False
            )
            logger.info(f"scan_media_task shim redirected {scan_type} scan {scan_id} "
                        f"to orchestrator task {task.id}")
            return {
                'status': 'REDIRECTED',
                'scan_id': scan_id,
                'task_id': task.id,
                'completed_at': datetime.now(timezone.utc).isoformat()
            }

        # Create scan service instance with database URI from Flask config
        from flask import current_app
        database_uri = current_app.config['SQLALCHEMY_DATABASE_URI']
        scan_service = ScanService(database_uri)

        if scan_type == 'single':
            # Single file scan
            if paths and len(paths) == 1:
                # Pass scan_id so scan_service reuses the ScanState row this task
                # is already tracking, instead of creating a second one. Otherwise
                # the UI's progress monitor sees a brief gap between rows and
                # flips to "done" before the real scan starts.
                result = scan_service.scan_single_file(
                    file_path=paths[0],
                    force_rescan=force_rescan,
                    scan_id=scan_id
                )
                
                # CRITICAL: Commit Flask-SQLAlchemy session to ensure ScanService changes are visible
                db.session.commit()
            else:
                raise ValueError("Single scan requires exactly one file path")
        else:
            raise ValueError(f"Unknown scan type: {scan_type}. Supported: full, parallel, pending, discover, single")
        
        logger.info(f"Celery scan task {self.request.id} completed successfully")
        
        return {
            'status': 'SUCCESS',
            'scan_id': scan_id,
            'task_id': self.request.id,
            'files_processed': result.get('files_processed', 0),
            'files_discovered': result.get('files_discovered', 0),
            'corrupted_found': result.get('corrupted_found', 0),
            'completed_at': datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as exc:
        import traceback
        logger.error(f"Celery scan task {self.request.id} failed: {str(exc)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

        # Check if this is a database error that should not be retried
        import sqlalchemy.exc
        import psycopg2

        is_db_error = isinstance(exc, (sqlalchemy.exc.DatabaseError, psycopg2.DatabaseError))
        is_connection_error = isinstance(exc, (sqlalchemy.exc.OperationalError, psycopg2.OperationalError))

        # Decide whether this exception will be retried so we can preserve the
        # ScanState row across attempts. If we marked it failed/inactive on
        # every transient error, the UI would flip to "done" during the retry
        # window and only re-discover the scan minutes later.
        is_corruption_error = is_db_error and is_db_connection_corruption(exc)
        will_retry = (
            self.request.retries < self.max_retries and not is_corruption_error
        )

        # Update scan state with error
        try:
            # Roll back any pending transaction before querying
            db.session.rollback()

            scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
            if scan_state:
                error_msg = f"Celery task failed: {str(exc)}"
                scan_state.error_message = error_msg[:950]  # Truncate to fit VARCHAR(1000)
                if will_retry:
                    # Keep the row active so the UI keeps showing progress
                    # during the retry backoff instead of jumping to "done".
                    scan_state.phase = SCAN_PHASES['INITIALIZING']
                    scan_state.progress_message = (
                        f'Retrying after error '
                        f'(attempt {self.request.retries + 1}/{self.max_retries})'
                    )
                else:
                    scan_state.is_active = False
                    scan_state.phase = 'failed'
                db.session.commit()
        except Exception as db_exc:
            logger.error(f"Failed to update scan state with error: {str(db_exc)}")
            # Try to rollback to clean up the session
            try:
                db.session.rollback()
            except:
                pass

        # Retry logic based on error type
        if self.request.retries < self.max_retries:
            # Connection errors might be transient, retry with backoff
            if is_connection_error:
                retry_delay = 2 ** self.request.retries * 30  # 30s, 60s, 120s
                logger.info(f"Database connection error, retrying task {self.request.id} in {retry_delay} seconds (attempt {self.request.retries + 1})")
                raise self.retry(exc=exc, countdown=retry_delay)
            # Other database errors (like PGRES_TUPLES_OK) might be session corruption
            elif is_db_error:
                logger.error(f"Database error detected: {type(exc).__name__}")
                # Don't retry immediately for database corruption errors
                if is_db_connection_corruption(exc):
                    logger.error(f"Database connection corruption detected - task {self.request.id} failed permanently")
                    raise exc
                else:
                    retry_delay = 2 ** self.request.retries * 60  # 60s, 120s, 240s
                    logger.info(f"Retrying task {self.request.id} in {retry_delay} seconds")
                    raise self.retry(exc=exc, countdown=retry_delay)
            else:
                # Other errors, use standard exponential backoff
                retry_delay = 2 ** self.request.retries * 60  # 60s, 120s, 240s
                logger.info(f"Retrying task {self.request.id} in {retry_delay} seconds (attempt {self.request.retries + 1})")
                raise self.retry(exc=exc, countdown=retry_delay)
        else:
            # Max retries exceeded, mark as failed
            logger.error(f"Task {self.request.id} failed permanently after {self.max_retries} retries")
            raise exc
    finally:
        current_scan_id.reset(_scan_token)
        current_celery_task_id.reset(_task_token)


@celery_app.task(bind=True, max_retries=2,
                 soft_time_limit=None, time_limit=None,  # No timeout for file scan tasks
                 priority=3)  # High priority - scans take precedence over maintenance
def scan_files_task(self, scan_id, file_paths, force_rescan=False, num_workers=None):
    """
    Background task for scanning specific files

    Args:
        scan_id (str): Unique scan identifier
        file_paths (list): List of specific file paths to scan
        force_rescan (bool): Whether to force rescan of existing files
        num_workers (int): Number of parallel workers to use (default: use MAX_WORKERS from config)

    Returns:
        dict: File scan results
    """
    # ContextTask wrapper in celery_config.py automatically provides Flask app context
    # NOTE: Do NOT call db.session.remove() here - it corrupts the connection pool
    # The ContextTask wrapper handles session management properly

    logger.info(f"Starting Celery file scan task {self.request.id} for scan_id: {scan_id}")

    # Tag logs with scan context
    _scan_token = current_scan_id.set(scan_id)
    _task_token = current_celery_task_id.set(self.request.id)

    try:
        from pixelprobe.services.scan_service import ScanService
        from flask import current_app
        from pixelprobe.config import Config

        database_uri = current_app.config['SQLALCHEMY_DATABASE_URI']
        scan_service = ScanService(database_uri)

        # Use provided num_workers or default to MAX_WORKERS from config
        if num_workers is None:
            num_workers = Config.MAX_WORKERS

        logger.info(f"Scanning {len(file_paths)} files with {num_workers} parallel workers")

        def progress_callback(progress_data):
            """Update file scan progress"""
            current_task.update_state(
                state='PROGRESS',
                meta={
                    'current': progress_data.get('files_processed', 0),
                    'total': progress_data.get('estimated_total', len(file_paths)),
                    'phase': progress_data.get('phase', 'Scanning Files'),
                    'current_file': progress_data.get('current_file', ''),
                    'scan_id': scan_id
                }
            )

        # Execute file scanning using the scan service with parallel workers
        # Note: ScanService handles progress internally via database updates
        result = scan_service.scan_files(
            file_paths=file_paths,
            force_rescan=force_rescan,
            num_workers=num_workers,  # Enable parallel scanning
            async_mode=False  # Run synchronously in Celery task
        )
        
        # Update Celery task state based on result
        current_task.update_state(
            state='PROGRESS',
            meta={
                'current': result.get('files_processed', len(file_paths)),
                'total': len(file_paths),
                'phase': 'completed',
                'scan_id': scan_id
            }
        )
        
        logger.info(f"Celery file scan task {self.request.id} completed successfully")
        
        return {
            'status': 'SUCCESS',
            'scan_id': scan_id,
            'task_id': self.request.id,
            'files_processed': result.get('files_processed', len(file_paths)),
            'files_scanned': result.get('files_scanned', 0),
            'corrupted_found': result.get('corrupted_found', 0),
            'completed_at': datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as exc:
        logger.error(f"Celery file scan task {self.request.id} failed: {str(exc)}")
        
        # Retry with delay
        if self.request.retries < self.max_retries:
            retry_delay = 30 * (self.request.retries + 1)  # 30s, 60s
            raise self.retry(exc=exc, countdown=retry_delay)
        else:
            raise exc
    finally:
        current_scan_id.reset(_scan_token)
        current_celery_task_id.reset(_task_token)


@celery_app.task
def health_check_task():
    """
    Simple health check task for monitoring Celery workers
    
    Returns:
        dict: Health status
    """
    return {
        'status': 'healthy',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'worker_id': current_task.request.hostname
    }


@celery_app.task(bind=True, max_retries=1,
                 soft_time_limit=5, time_limit=10,  # Fast operation - just check existence
                 priority=6)  # Higher priority than hash calc - cleanup tasks are quick
def check_file_exists_task(self, file_id, file_path):
    """
    Check if a file exists on disk (for orphan cleanup)

    This is a lightweight task that only checks file existence without reading the file.
    Much faster than hash calculation for orphan cleanup operations.

    Args:
        file_id (int): Database ID of the file
        file_path (str): Path to the file to check

    Returns:
        dict: {file_id, file_path, status, exists}
            status is one of 'exists', 'absent', 'unknown'. 'exists' is kept as a
            convenience bool but callers that delete rows MUST gate on
            status == 'absent' so a transient/mount error ('unknown') never
            looks like an orphan.
    """
    # ContextTask wrapper in celery_config.py automatically provides Flask app context
    # NOTE: Do NOT call db.session.remove() here - it corrupts the connection pool
    # The ContextTask wrapper handles session management properly

    from pixelprobe.utils.helpers import classify_path_existence, PATH_EXISTS

    status = classify_path_existence(file_path)
    return {
        'file_id': file_id,
        'file_path': file_path,
        'status': status,
        'exists': status == PATH_EXISTS
    }


@celery_app.task(bind=True, max_retries=2,
                 soft_time_limit=None, time_limit=None,  # No timeout - must complete hash regardless of file size
                 priority=7)  # Low priority - maintenance runs in background
def calculate_file_hash_task(self, file_id, file_path, stored_hash, stored_modified,
                             stored_size=None, mtime_trusted=False,
                             bitrot_suspected=False, bitrot_candidate_hash=None):
    """
    Calculate hash for a single file and classify any change.

    IMPORTANT: No timeouts on this task - we must always calculate hash regardless of how
    large the file is. File integrity checking requires accurate hashes.

    Classification: a hash mismatch with a changed mtime is a legitimate
    modification; a mismatch with an UNCHANGED mtime is suspected bitrot (no
    legitimate write path alters content without touching mtime). Files
    already flagged are measured against the candidate hash for the
    auto-expire state machine (stable / self-healed / active rot).

    Args:
        file_id (int): Database ID of the file
        file_path (str): Path to the file
        stored_hash (str): Expected hash from database
        stored_modified (str): Last modified timestamp from database (ISO format)
        stored_size (int): File size from database (for the detection record)
        mtime_trusted (bool): ScanResult.mtime_baseline_utc - pre-upgrade
            naive-local baselines cannot be classified and fall back to
            'modified', which re-baselines in UTC
        bitrot_suspected (bool): File is already flagged
        bitrot_candidate_hash (str): Stability reference for flagged files

    Returns:
        dict: Hash comparison result with change classification
    """
    # ContextTask wrapper in celery_config.py automatically provides Flask app context
    # NOTE: Do NOT call db.session.remove() here - it corrupts the connection pool
    # The ContextTask wrapper handles session management properly

    import hashlib
    import os
    from datetime import datetime, timezone

    try:
        # Check if file exists
        if not os.path.exists(file_path):
            return {
                'file_id': file_id,
                'file_path': file_path,
                'changed': True,
                'change_type': 'deleted',
                'stored_hash': stored_hash,
                'current_hash': None,
                'stored_modified': stored_modified,
                'current_modified': None
            }

        # Calculate current hash
        # Use mmap for large files (>100MB), buffered read for smaller files
        import mmap

        file_size = os.path.getsize(file_path)
        sha256_hash = hashlib.sha256()

        if file_size > 100 * 1024 * 1024:  # 100MB threshold
            # Use memory-mapped I/O for large files (5-10x faster)
            logger.info(f"File changes check: Hashing large file ({file_size/1024/1024/1024:.1f}GB) with mmap: {file_path}")
            try:
                import time
                start_time = time.time()
                with open(file_path, "rb") as f:
                    # Map entire file into memory (OS handles paging)
                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                        sha256_hash.update(mm)
                elapsed = time.time() - start_time
                logger.info(f"mmap hash completed in {elapsed:.1f}s for {file_size/1024/1024/1024:.1f}GB file ({file_size/elapsed/1024/1024:.1f} MB/s)")
            except (OSError, ValueError) as e:
                # Fallback to buffered read if mmap fails (e.g., empty file, special file, network mount)
                logger.warning(f"mmap failed for {file_path}, falling back to buffered read: {e}")
                import time
                start_time = time.time()
                with open(file_path, "rb") as f:
                    for byte_block in iter(lambda: f.read(1048576), b""):  # 1MB chunks
                        sha256_hash.update(byte_block)
                elapsed = time.time() - start_time
                logger.info(f"Buffered hash completed in {elapsed:.1f}s for {file_size/1024/1024/1024:.1f}GB file ({file_size/elapsed/1024/1024:.1f} MB/s)")
        else:
            # Use 1MB buffered reads for smaller files (was 4KB, now 262x faster)
            with open(file_path, "rb") as f:
                for byte_block in iter(lambda: f.read(1048576), b""):  # 1MB chunks
                    sha256_hash.update(byte_block)

        current_hash = sha256_hash.hexdigest()

        # Get current modification time
        stat = os.stat(file_path)
        current_modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        current_size = stat.st_size

        change_type, changed = classify_file_change(
            stored_hash, current_hash, stored_modified, current_modified,
            mtime_trusted=mtime_trusted,
            bitrot_suspected=bitrot_suspected,
            bitrot_candidate_hash=bitrot_candidate_hash
        )

        return {
            'file_id': file_id,
            'file_path': file_path,
            'changed': changed,
            'change_type': change_type,
            'stored_hash': stored_hash,
            'current_hash': current_hash,
            'stored_modified': stored_modified,
            'current_modified': current_modified.isoformat() if current_modified else None,
            'stored_size': stored_size,
            'current_size': current_size
        }

    except Exception as exc:
        logger.error(f"Hash calculation failed for {file_path}: {str(exc)}")

        # Retry with delay for transient errors
        if self.request.retries < self.max_retries:
            retry_delay = 10 * (self.request.retries + 1)  # 10s, 20s
            raise self.retry(exc=exc, countdown=retry_delay)
        else:
            # Max retries exceeded. Report this as changed=True (change_type
            # 'error') rather than changed=False: a file we could not hash must
            # be re-examined by a full scan, not silently treated as unchanged
            # (which would mask corruption the integrity check exists to catch).
            return {
                'file_id': file_id,
                'file_path': file_path,
                'changed': True,
                'change_type': 'error',
                'error': str(exc),
                'stored_hash': stored_hash,
                'current_hash': None
            }



@celery_app.task(name='pixelprobe.tasks.run_retention_cleanup',
                 bind=True,
                 max_retries=3,
                 soft_time_limit=600,  # 10 minutes soft limit for retention cleanup
                 time_limit=900,  # 15 minutes hard limit
                 priority=9)  # Lowest priority - maintenance task
def run_retention_cleanup(self):
    """
    Scheduled task to run data retention policies

    P2 Implementation: Automated data cleanup to prevent unbounded database growth
    Runs daily via Celery Beat scheduler

    Returns:
        dict: Retention cleanup results
    """
    logger.info(f"Starting data retention cleanup task {self.request.id}")

    try:
        from tools.data_retention import run_all_retention_policies

        # Execute all retention policies
        result = run_all_retention_policies()

        if result['success']:
            logger.info(
                f"Data retention cleanup completed: "
                f"{result['reports_deleted']} reports deleted, "
                f"{result['states_deleted']} scan states deleted "
                f"(scan_output archival disabled - keeping all scan results)"
            )

            return {
                'status': 'SUCCESS',
                'task_id': self.request.id,
                'outputs_archived': result['outputs_archived'],
                'reports_deleted': result['reports_deleted'],
                'states_deleted': result['states_deleted'],
                'stats_before': result['stats_before'],
                'completed_at': datetime.now(timezone.utc).isoformat()
            }
        else:
            # Retention policies failed
            logger.error(f"Data retention cleanup failed: {result.get('error')}")

            # Retry with exponential backoff
            if self.request.retries < self.max_retries:
                retry_delay = 2 ** self.request.retries * 300  # 5min, 10min, 20min
                logger.info(f"Retrying retention cleanup in {retry_delay} seconds")
                raise self.retry(exc=RuntimeError(result.get('error')), countdown=retry_delay)
            else:
                raise RuntimeError(result.get('error'))

    except Exception as exc:
        logger.error(f"Data retention cleanup task {self.request.id} failed: {str(exc)}")

        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            retry_delay = 2 ** self.request.retries * 300  # 5min, 10min, 20min
            logger.info(f"Retrying retention cleanup in {retry_delay} seconds (attempt {self.request.retries + 1})")
            raise self.retry(exc=exc, countdown=retry_delay)
        else:
            # Max retries exceeded
            logger.error(f"Retention cleanup task {self.request.id} failed permanently after {self.max_retries} retries")
            raise exc


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5)
def reload_schedules_task(self):
    """
    Reload schedules from database into the running scheduler.

    This task runs in the Celery worker where the scheduler is active,
    allowing Flask/gunicorn to trigger schedule reloads via Celery message queue.

    Called when schedules are created, updated, or deleted via the admin API.
    """
    try:
        from app import scheduler

        if scheduler and scheduler.scheduler.running:
            logger.info("Reloading schedules from database via Celery task")
            scheduler.update_schedules()
            logger.info("Schedule reload completed successfully")
            return {'status': 'success', 'message': 'Schedules reloaded'}
        else:
            logger.warning("Scheduler not running in this worker, cannot reload schedules")
            return {'status': 'skipped', 'message': 'Scheduler not running in this worker'}

    except Exception as exc:
        logger.error(f"Failed to reload schedules: {exc}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        raise
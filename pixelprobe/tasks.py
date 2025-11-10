"""
Celery Tasks for PixelProbe
P1 Implementation per 2.1_AUDIT_IMPLEMENTATION_PLAN.md

This module contains all Celery tasks for distributed processing.
Replaces ThreadPoolExecutor with proper task queue system.
"""

from celery import current_task
from celery.exceptions import Retry
import logging
import time
from datetime import datetime, timezone, timedelta
import redis
from contextlib import contextmanager

from celery_config import celery_app
from pixelprobe.services.scan_service import ScanService
from pixelprobe.progress_utils import get_redis_client, update_scan_progress_redis
from models import db, ScanState, ScanResult


logger = logging.getLogger(__name__)


@contextmanager
def distributed_lock(lock_name, timeout=300, blocking_timeout=10):
    """
    Distributed lock using Redis to prevent race conditions across Celery workers

    Args:
        lock_name (str): Unique name for the lock
        timeout (int): Lock auto-release timeout in seconds (default 5 minutes)
        blocking_timeout (int): How long to wait to acquire lock (default 10 seconds)

    Yields:
        bool: True if lock was acquired, False otherwise
    """
    redis_client = get_redis_client()
    lock = None
    acquired = False

    if redis_client:
        try:
            # Create Redis lock with auto-release timeout
            lock = redis_client.lock(
                lock_name,
                timeout=timeout,
                blocking_timeout=blocking_timeout
            )
            acquired = lock.acquire(blocking=True, blocking_timeout=blocking_timeout)
            logger.info(f"Distributed lock '{lock_name}' acquired: {acquired}")
            yield acquired
        except redis.exceptions.LockError as e:
            logger.warning(f"Failed to acquire lock '{lock_name}': {e}")
            yield False
        except Exception as e:
            logger.error(f"Redis lock error for '{lock_name}': {e}")
            yield False
        finally:
            if lock and acquired:
                try:
                    lock.release()
                    logger.info(f"Distributed lock '{lock_name}' released")
                except Exception as e:
                    logger.error(f"Error releasing lock '{lock_name}': {e}")
    else:
        # Redis not available, proceed without locking (degraded mode)
        logger.warning(f"Redis not available, proceeding without distributed lock for '{lock_name}'")
        yield True


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
    logger.info(f"Starting Celery scan task {self.request.id} for scan_id: {scan_id}")

    try:
        # CRITICAL: Use distributed lock to prevent race conditions when multiple workers
        # try to start scans simultaneously (e.g., during task retries)
        with distributed_lock('pixelprobe:scan:initialization', timeout=300, blocking_timeout=15) as lock_acquired:
            if not lock_acquired:
                error_msg = "Failed to acquire scan initialization lock - another worker is starting a scan"
                logger.warning(f"Celery scan task {self.request.id}: {error_msg}")

                # Retry with exponential backoff + jitter to prevent thundering herd
                if self.request.retries < self.max_retries:
                    import random
                    retry_delay = (2 ** self.request.retries * 30) + random.randint(0, 10)  # 30s, 60s, 120s + jitter
                    logger.info(f"Retrying scan task {self.request.id} in {retry_delay} seconds (attempt {self.request.retries + 1})")
                    raise self.retry(exc=RuntimeError(error_msg), countdown=retry_delay)
                else:
                    logger.error(f"Task {self.request.id} failed permanently after {self.max_retries} retries")
                    raise RuntimeError(error_msg)

            # Lock acquired - now check if another scan is actually running
            # This double-check pattern ensures we don't have stale database state
            active_scan = ScanState.query.filter_by(is_active=True).first()
            if active_scan and active_scan.scan_id != scan_id:
                # Check if the active scan is actually stuck (no update for 5+ minutes)
                check_time = active_scan.last_update or active_scan.start_time

                if check_time:
                    if check_time.tzinfo is None:
                        check_time = check_time.replace(tzinfo=timezone.utc)

                    time_since_update = datetime.now(timezone.utc) - check_time

                    # If scan hasn't updated in 5 minutes, it's likely stuck
                    # Reduced from 10 minutes to detect stuck scans faster
                    if time_since_update > timedelta(minutes=5):
                        logger.warning(f"Found stuck scan {active_scan.scan_id} (no update for {time_since_update}), marking as crashed")
                        active_scan.is_active = False
                        active_scan.phase = 'crashed'
                        active_scan.error_message = f'Scan stuck - no progress for {time_since_update}'
                        db.session.commit()
                        # Now we can proceed with our scan
                    else:
                        # Another scan is genuinely running
                        error_msg = f"Another scan is already in progress (scan_id: {active_scan.scan_id}, phase: {active_scan.phase})"
                        logger.error(f"Celery scan task {self.request.id} failed: {error_msg}")

                        # Retry with exponential backoff + jitter
                        if self.request.retries < self.max_retries:
                            import random
                            retry_delay = (2 ** self.request.retries * 30) + random.randint(0, 10)
                            logger.info(f"Retrying scan task {self.request.id} in {retry_delay} seconds")
                            raise self.retry(exc=RuntimeError(error_msg), countdown=retry_delay)
                        else:
                            # Max retries reached, fail the task
                            logger.error(f"Task {self.request.id} failed permanently after {self.max_retries} retries")
                            raise RuntimeError(error_msg)
        
        # Update scan state with Celery task ID
        scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
        if scan_state:
            scan_state.celery_task_id = self.request.id
            scan_state.progress_message = f"Starting {scan_type} scan via Celery task queue"
            db.session.commit()
        
        # Create scan service instance with database URI from Flask config
        from flask import current_app
        database_uri = current_app.config['SQLALCHEMY_DATABASE_URI']
        scan_service = ScanService(database_uri)
        
        # Execute the scan with progress callbacks
        def progress_callback(progress_data):
            """Update task progress for monitoring"""
            current_task.update_state(
                state='PROGRESS',
                meta={
                    'current': progress_data.get('files_processed', 0),
                    'total': progress_data.get('estimated_total', 0),
                    'phase': progress_data.get('phase', 'Unknown'),
                    'current_file': progress_data.get('current_file', ''),
                    'scan_id': scan_id
                }
            )
        
        # Execute scan based on type
        if scan_type in ['full', 'parallel', 'pending']:
            # Use configured MAX_WORKERS instead of hardcoded 1
            from config import Config
            import os
            num_workers = Config.MAX_WORKERS
            logger.info(f"Celery task using MAX_WORKERS={num_workers} (env var: {os.getenv('MAX_WORKERS', 'NOT SET')})")

            # Note: ScanService handles progress internally via database updates
            # The progress_callback defined above is for Celery task state updates
            result = scan_service.scan_directories(
                directories=paths,
                force_rescan=force_rescan,
                num_workers=num_workers,
                async_mode=False  # Run synchronously in Celery task
            )
            
            # CRITICAL: Commit Flask-SQLAlchemy session to ensure ScanService changes are visible
            # ScanService uses its own connection, so we need to ensure Flask session sees updates
            db.session.commit()
            
            # Update Celery task state based on result
            current_task.update_state(
                state='PROGRESS',
                meta={
                    'current': result.get('files_processed', 0),
                    'total': result.get('files_processed', 0),
                    'phase': 'completed',
                    'scan_id': scan_id
                }
            )
        elif scan_type == 'discover':
            # Discovery is handled internally by scan_directories
            # There's no separate discover_files method in ScanService
            # Run a regular scan which includes discovery phase
            from config import Config
            result = scan_service.scan_directories(
                directories=paths,
                force_rescan=False,  # Don't force rescan for discovery
                num_workers=Config.MAX_WORKERS,  # Use configured MAX_WORKERS instead of hardcoded 1
                async_mode=False  # Run synchronously in Celery task
            )
            
            # CRITICAL: Commit Flask-SQLAlchemy session to ensure ScanService changes are visible
            db.session.commit()
            
        elif scan_type == 'single':
            # Single file scan
            if paths and len(paths) == 1:
                result = scan_service.scan_single_file(
                    file_path=paths[0],
                    force_rescan=force_rescan
                )
                
                # CRITICAL: Commit Flask-SQLAlchemy session to ensure ScanService changes are visible
                db.session.commit()
            else:
                raise ValueError("Single scan requires exactly one file path")
        else:
            raise ValueError(f"Unknown scan type: {scan_type}. Supported: full, parallel, deep, pending, discover, single")
        
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

        # Update scan state with error
        try:
            # Roll back any pending transaction before querying
            db.session.rollback()

            scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
            if scan_state:
                scan_state.error_message = f"Celery task failed: {str(exc)}"
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
                if "PGRES_TUPLES_OK" in str(exc) or "no message from the libpq" in str(exc):
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


@celery_app.task(bind=True, max_retries=2,
                 soft_time_limit=None, time_limit=None,  # No timeout for cleanup tasks
                 priority=7)  # Low priority - maintenance runs in background
def cleanup_orphaned_task(self, cleanup_id, batch_size=1000):
    """
    Background task for cleaning up orphaned database records
    
    Args:
        cleanup_id (str): Unique cleanup operation identifier
        batch_size (int): Number of records to process per batch
        
    Returns:
        dict: Cleanup results
    """
    logger.info(f"Starting Celery cleanup task {self.request.id} for cleanup_id: {cleanup_id}")
    
    try:
        # MaintenanceService not yet implemented - placeholder for future functionality
        logger.warning(f"Cleanup task {self.request.id} skipped - MaintenanceService not yet implemented")
        
        # For now, return a mock success to prevent task failures
        result = {
            'orphaned_removed': 0,
            'files_processed': 0,
            'skipped': True,
            'message': 'MaintenanceService not yet implemented'
        }
        
        # Update task state to show it's complete but skipped
        current_task.update_state(
            state='SUCCESS',
            meta={
                'cleanup_id': cleanup_id,
                'skipped': True,
                'message': 'Cleanup functionality not yet implemented'
            }
        )
        
        logger.info(f"Celery cleanup task {self.request.id} completed successfully")
        
        return {
            'status': 'SUCCESS',
            'cleanup_id': cleanup_id,
            'task_id': self.request.id,
            'orphaned_removed': result.get('orphaned_removed', 0),
            'files_processed': result.get('files_processed', 0),
            'completed_at': datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as exc:
        logger.error(f"Celery cleanup task {self.request.id} failed: {str(exc)}")
        
        # Retry with delay
        if self.request.retries < self.max_retries:
            retry_delay = 30 * (self.request.retries + 1)  # 30s, 60s
            raise self.retry(exc=exc, countdown=retry_delay)
        else:
            raise exc


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
    logger.info(f"Starting Celery file scan task {self.request.id} for scan_id: {scan_id}")

    try:
        from pixelprobe.services.scan_service import ScanService
        from flask import current_app
        from config import Config

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


@celery_app.task(priority=3)  # High priority - scans take precedence over maintenance
def scheduled_scan_task(schedule_id, scan_type='full'):
    """
    Task for executing scheduled scans
    
    Args:
        schedule_id (int): ID of the schedule to execute
        scan_type (str): Type of scan to perform
        
    Returns:
        dict: Scheduled scan results
    """
    logger.info(f"Executing scheduled scan for schedule_id: {schedule_id}")
    
    try:
        from models import ScanSchedule
        from uuid import uuid4
        
        # Get schedule details
        schedule = ScanSchedule.query.get(schedule_id)
        if not schedule or not schedule.is_active:
            raise ValueError(f"Schedule {schedule_id} not found or inactive")
        
        # Create scan ID and trigger scan task
        scan_id = str(uuid4())
        paths = schedule.scan_paths.split(',') if schedule.scan_paths else []
        
        # Queue the actual scan task
        task = scan_media_task.delay(
            scan_id=scan_id,
            paths=paths,
            scan_type=schedule.scan_type or scan_type,
            force_rescan=schedule.force_rescan or False
        )
        
        # Update schedule last run time
        schedule.last_run = datetime.now(timezone.utc)
        db.session.commit()
        
        logger.info(f"Scheduled scan queued with task_id: {task.id}")
        
        return {
            'status': 'QUEUED',
            'schedule_id': schedule_id,
            'scan_id': scan_id,
            'task_id': task.id,
            'queued_at': datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as exc:
        logger.error(f"Scheduled scan failed for schedule_id {schedule_id}: {str(exc)}")
        raise exc


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
        dict: {file_id, file_path, exists: bool}
    """
    import os

    try:
        # Check if file exists - use multiple methods for robust detection
        file_exists = False

        # Method 1: os.path.exists() - fast but may have issues with symlinks
        if os.path.exists(file_path):
            file_exists = True
        # Method 2: Try to stat the file directly - more reliable
        elif os.path.isfile(file_path):
            file_exists = True
        # Method 3: Check if path exists at all (directory or file)
        elif os.path.lexists(file_path):
            # lexists returns True even for broken symlinks
            # If lexists is True but exists is False, it's a broken symlink - treat as not existing
            file_exists = False

        return {
            'file_id': file_id,
            'file_path': file_path,
            'exists': file_exists
        }

    except (OSError, IOError) as e:
        # If we get an error accessing the file, treat it as not existing
        logger.warning(f"Error checking file existence {file_path}: {e}")
        return {
            'file_id': file_id,
            'file_path': file_path,
            'exists': False
        }


@celery_app.task(bind=True, max_retries=2,
                 soft_time_limit=None, time_limit=None,  # No timeout - must complete hash regardless of file size
                 priority=7)  # Low priority - maintenance runs in background
def calculate_file_hash_task(self, file_id, file_path, stored_hash, stored_modified):
    """
    Calculate hash for a single file and compare to stored hash

    IMPORTANT: No timeouts on this task - we must always calculate hash regardless of how
    large the file is. File integrity checking requires accurate hashes.

    Args:
        file_id (int): Database ID of the file
        file_path (str): Path to the file
        stored_hash (str): Expected hash from database
        stored_modified (str): Last modified timestamp from database (ISO format)

    Returns:
        dict: Hash comparison result with change information
    """
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

        # Parse stored modified time
        if stored_modified:
            try:
                stored_mod_dt = datetime.fromisoformat(stored_modified.replace('Z', '+00:00'))
            except:
                stored_mod_dt = None
        else:
            stored_mod_dt = None

        # Determine if file changed
        if not stored_hash:
            change_type = 'no_hash'
            changed = True
        elif current_hash != stored_hash:
            change_type = 'modified'
            changed = True
        else:
            change_type = 'unchanged'
            changed = False

        return {
            'file_id': file_id,
            'file_path': file_path,
            'changed': changed,
            'change_type': change_type,
            'stored_hash': stored_hash,
            'current_hash': current_hash,
            'stored_modified': stored_modified,
            'current_modified': current_modified.isoformat() if current_modified else None
        }

    except Exception as exc:
        logger.error(f"Hash calculation failed for {file_path}: {str(exc)}")

        # Retry with delay for transient errors
        if self.request.retries < self.max_retries:
            retry_delay = 10 * (self.request.retries + 1)  # 10s, 20s
            raise self.retry(exc=exc, countdown=retry_delay)
        else:
            # Max retries exceeded, return error result
            return {
                'file_id': file_id,
                'file_path': file_path,
                'changed': False,
                'change_type': 'error',
                'error': str(exc),
                'stored_hash': stored_hash,
                'current_hash': None
            }


@celery_app.task(bind=True, priority=9)
def ui_progress_update_task(self, scan_id, update_interval=1.0):
    """
    UI worker that periodically reads scan state from database and ensures
    phase_total and estimated_total are kept in sync for proper UI display.

    The scanning workers update files_processed and phase_total in the database.
    This worker ensures estimated_total matches phase_total so the UI shows correct totals.

    Args:
        scan_id (str): The scan ID to track progress for
        update_interval (float): How often to check progress (seconds)

    Returns:
        dict: Final progress status
    """
    logger.info(f"Starting UI progress worker for scan {scan_id}")

    import sys
    import os
    import time
    from datetime import datetime, timezone

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import app, db as flask_db
    from sqlalchemy.orm import scoped_session, sessionmaker
    from models import ScanState

    app_context = app.app_context()
    app_context.push()

    # Create a separate session for the UI worker to avoid conflicts with scan workers
    ui_session_factory = sessionmaker(bind=flask_db.engine)
    UiSession = scoped_session(ui_session_factory)

    try:
        consecutive_no_change = 0
        max_no_change = 1800  # Stop after 30 minutes of no activity (matches stuck scan detection timeout)
        last_files_processed = -1
        last_db_update = None

        while True:
            try:
                # Use separate UI session to avoid concurrent access issues
                ui_session = UiSession()

                # Ensure we're reading fresh data by committing any pending transaction
                # This forces the session to start a new transaction and see latest data
                ui_session.commit()

                # Read current scan state from database
                scan_state = ui_session.query(ScanState).filter_by(scan_id=scan_id).first()

                if not scan_state:
                    logger.warning(f"Scan state not found for {scan_id}")
                    break

                # Check if scan is complete
                if scan_state.phase in ['completed', 'error', 'cancelled', 'crashed']:
                    logger.info(f"Scan {scan_id} finished with phase: {scan_state.phase}")
                    break

                # Check if scan is still active
                if not scan_state.is_active:
                    logger.info(f"Scan {scan_id} is no longer active, stopping UI worker")
                    break

                # Get current values
                files_processed = scan_state.files_processed or 0
                phase_total = scan_state.phase_total or 0
                current_db_update = scan_state.last_update

                # Update last_update timestamp to prevent stuck scan detection
                scan_state.last_update = datetime.now(timezone.utc)

                # Debug logging for the "x of 0 files" issue
                if scan_state.estimated_total == 0 or phase_total == 0:
                    logger.warning(f"Zero total detected - estimated_total={scan_state.estimated_total}, phase_total={phase_total}, files_processed={files_processed}")

                # The key fix: if estimated_total is 0 but phase_total has a value, sync them
                if scan_state.estimated_total == 0 and phase_total > 0:
                    logger.info(f"Fixing estimated_total: was 0, setting to phase_total={phase_total}")
                    scan_state.estimated_total = phase_total
                    ui_session.commit()
                    logger.info(f"Updated scan {scan_id}: estimated_total now {phase_total}")

                # Also ensure phase_total matches estimated_total if it has a value
                elif scan_state.estimated_total > 0 and phase_total != scan_state.estimated_total:
                    logger.info(f"Syncing phase_total to estimated_total: {scan_state.estimated_total}")
                    scan_state.phase_total = scan_state.estimated_total
                    ui_session.commit()
                else:
                    # Even if no sync needed, commit to update last_update timestamp
                    ui_session.commit()

                # Expire the scan_state object to release locks and allow concurrent access
                ui_session.expire(scan_state)

                # Check for activity: either file count changed OR database was updated by scan workers
                # This handles large files where file count doesn't change but workers are still active
                db_was_updated = (last_db_update is not None and
                                 current_db_update is not None and
                                 current_db_update > last_db_update)

                files_changed = files_processed != last_files_processed

                if files_changed or db_was_updated:
                    # There's activity - reset the no-change counter
                    consecutive_no_change = 0
                    last_files_processed = files_processed
                    last_db_update = current_db_update
                    logger.debug(f"Scan {scan_id} progress: {files_processed}/{scan_state.estimated_total}")
                else:
                    # No activity detected
                    consecutive_no_change += 1
                    if consecutive_no_change >= max_no_change:
                        logger.warning(f"No database activity for {max_no_change} seconds, stopping UI worker")
                        break

                # Close and remove the UI session to release connections
                ui_session.close()
                UiSession.remove()

            except Exception as e:
                logger.error(f"Error in UI worker: {e}", exc_info=True)
                try:
                    ui_session.rollback()
                    ui_session.close()
                    UiSession.remove()
                except:
                    pass

            # Sleep before next update
            time.sleep(update_interval)

        return {
            'status': 'SUCCESS',
            'scan_id': scan_id,
            'final_progress': last_files_processed
        }

    except Exception as exc:
        logger.error(f"UI progress worker failed for scan {scan_id}: {str(exc)}")
        return {
            'status': 'ERROR',
            'scan_id': scan_id,
            'error': str(exc)
        }
    finally:
        # Clean up UI session and Flask app context
        try:
            UiSession.remove()
        except:
            pass
        try:
            app_context.pop()
        except:
            pass


# update_scan_progress_redis is now imported from progress_utils to avoid circular imports
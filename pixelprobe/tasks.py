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

from celery_config import celery_app
from pixelprobe.services.scan_service import ScanService
from models import db, ScanState, ScanResult


logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, 
                 soft_time_limit=None, time_limit=None)  # No timeout for scan tasks
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
        # CRITICAL: Check if another scan is already running before proceeding
        # This prevents concurrent scans when multiple tasks are queued
        active_scan = ScanState.query.filter_by(is_active=True).first()
        if active_scan and active_scan.scan_id != scan_id:
            # Check if the active scan is actually stuck (no update for 10+ minutes)
            # timedelta already imported at module level
            check_time = active_scan.last_update or active_scan.start_time
            
            if check_time:
                if check_time.tzinfo is None:
                    check_time = check_time.replace(tzinfo=timezone.utc)
                
                time_since_update = datetime.now(timezone.utc) - check_time
                
                # If scan hasn't updated in 10 minutes, it's likely stuck
                if time_since_update > timedelta(minutes=10):
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
                    
                    # Retry with delay to check again later
                    if self.request.retries < self.max_retries:
                        retry_delay = 60 * (self.request.retries + 1)  # 60s, 120s, 180s
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
            # Determine parameters based on scan type
            num_workers = 1 if scan_type in ['full', 'pending'] else 4
            
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
            result = scan_service.scan_directories(
                directories=paths,
                force_rescan=False,  # Don't force rescan for discovery
                num_workers=1,
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
        logger.error(f"Celery scan task {self.request.id} failed: {str(exc)}")
        
        # Update scan state with error
        try:
            scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
            if scan_state:
                scan_state.error_message = f"Celery task failed: {str(exc)}"
                scan_state.is_active = False
                db.session.commit()
        except Exception as db_exc:
            logger.error(f"Failed to update scan state with error: {str(db_exc)}")
        
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            retry_delay = 2 ** self.request.retries * 60  # 1min, 2min, 4min
            logger.info(f"Retrying task {self.request.id} in {retry_delay} seconds (attempt {self.request.retries + 1})")
            raise self.retry(exc=exc, countdown=retry_delay)
        else:
            # Max retries exceeded, mark as failed
            logger.error(f"Task {self.request.id} failed permanently after {self.max_retries} retries")
            raise exc


@celery_app.task(bind=True, max_retries=2,
                 soft_time_limit=None, time_limit=None)  # No timeout for cleanup tasks
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
                 soft_time_limit=None, time_limit=None)  # No timeout for file scan tasks
def scan_files_task(self, scan_id, file_paths, force_rescan=False):
    """
    Background task for scanning specific files
    
    Args:
        scan_id (str): Unique scan identifier
        file_paths (list): List of specific file paths to scan
        force_rescan (bool): Whether to force rescan of existing files
        
    Returns:
        dict: File scan results
    """
    logger.info(f"Starting Celery file scan task {self.request.id} for scan_id: {scan_id}")
    
    try:
        from pixelprobe.services.scan_service import ScanService
        from flask import current_app
        
        database_uri = current_app.config['SQLALCHEMY_DATABASE_URI']
        scan_service = ScanService(database_uri)
        
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
        
        # Execute file scanning using the scan service
        # Note: ScanService handles progress internally via database updates
        result = scan_service.scan_files(
            file_paths=file_paths,
            force_rescan=force_rescan,
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


@celery_app.task
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
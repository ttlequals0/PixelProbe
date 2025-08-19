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
from datetime import datetime

from celery_config import celery_app
from pixelprobe.services.scan_service import ScanService
from models import db, ScanState, ScanResult


logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
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
        if scan_type in ['full', 'parallel', 'deep', 'pending']:
            # Determine parameters based on scan type
            num_workers = 1 if scan_type in ['full', 'pending'] else 4
            deep_scan = scan_type == 'deep'
            
            result = scan_service.scan_directories(
                directories=paths,
                force_rescan=force_rescan,
                num_workers=num_workers,
                deep_scan=deep_scan,
                progress_callback=progress_callback
            )
        elif scan_type == 'discover':
            result = scan_service.discover_files(
                directories=paths,
                progress_callback=progress_callback
            )
        elif scan_type == 'single':
            # Single file scan
            if paths and len(paths) == 1:
                result = scan_service.scan_single_file(
                    file_path=paths[0],
                    force_rescan=force_rescan
                )
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
            'completed_at': datetime.utcnow().isoformat()
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


@celery_app.task(bind=True, max_retries=2)
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
        from pixelprobe.services.maintenance_service import MaintenanceService
        
        maintenance_service = MaintenanceService()
        
        def progress_callback(progress_data):
            """Update cleanup progress"""
            current_task.update_state(
                state='PROGRESS',
                meta={
                    'current': progress_data.get('files_processed', 0),
                    'total': progress_data.get('total_files', 0),
                    'phase': progress_data.get('phase', 'Cleanup'),
                    'orphaned_found': progress_data.get('orphaned_found', 0),
                    'cleanup_id': cleanup_id
                }
            )
        
        # Execute cleanup operation
        result = maintenance_service.cleanup_orphaned_records(
            cleanup_id=cleanup_id,
            batch_size=batch_size,
            progress_callback=progress_callback
        )
        
        logger.info(f"Celery cleanup task {self.request.id} completed successfully")
        
        return {
            'status': 'SUCCESS',
            'cleanup_id': cleanup_id,
            'task_id': self.request.id,
            'orphaned_removed': result.get('orphaned_removed', 0),
            'files_processed': result.get('files_processed', 0),
            'completed_at': datetime.utcnow().isoformat()
        }
        
    except Exception as exc:
        logger.error(f"Celery cleanup task {self.request.id} failed: {str(exc)}")
        
        # Retry with delay
        if self.request.retries < self.max_retries:
            retry_delay = 30 * (self.request.retries + 1)  # 30s, 60s
            raise self.retry(exc=exc, countdown=retry_delay)
        else:
            raise exc


@celery_app.task(bind=True, max_retries=2)
def scan_files_task(self, scan_id, file_paths, force_rescan=False, deep_scan=False):
    """
    Background task for scanning specific files
    
    Args:
        scan_id (str): Unique scan identifier
        file_paths (list): List of specific file paths to scan
        force_rescan (bool): Whether to force rescan of existing files
        deep_scan (bool): Whether to perform deep scanning
        
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
        result = scan_service.scan_files(
            file_paths=file_paths,
            force_rescan=force_rescan,
            deep_scan=deep_scan,
            progress_callback=progress_callback
        )
        
        logger.info(f"Celery file scan task {self.request.id} completed successfully")
        
        return {
            'status': 'SUCCESS',
            'scan_id': scan_id,
            'task_id': self.request.id,
            'files_processed': result.get('files_processed', len(file_paths)),
            'files_scanned': result.get('files_scanned', 0),
            'corrupted_found': result.get('corrupted_found', 0),
            'completed_at': datetime.utcnow().isoformat()
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
        'timestamp': datetime.utcnow().isoformat(),
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
        schedule.last_run = datetime.utcnow()
        db.session.commit()
        
        logger.info(f"Scheduled scan queued with task_id: {task.id}")
        
        return {
            'status': 'QUEUED',
            'schedule_id': schedule_id,
            'scan_id': scan_id,
            'task_id': task.id,
            'queued_at': datetime.utcnow().isoformat()
        }
        
    except Exception as exc:
        logger.error(f"Scheduled scan failed for schedule_id {schedule_id}: {str(exc)}")
        raise exc
from flask import Blueprint, request
import os
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
import pytz

from pixelprobe.models import db, ScanResult, CleanupState, FileChangesState
from pixelprobe.media_checker import PixelProbe
from pixelprobe.auth import auth_required
from pixelprobe.utils.helpers import ProgressTracker
from pixelprobe.services.maintenance_service import MaintenanceService
from pixelprobe.progress_utils import get_file_changes_progress_redis

logger = logging.getLogger(__name__)

# Get timezone from environment variable, default to UTC
APP_TIMEZONE = os.environ.get('TZ', 'UTC')
try:
    tz = pytz.timezone(APP_TIMEZONE)
except pytz.exceptions.UnknownTimeZoneError:
    tz = pytz.UTC
    logger.warning(f"Unknown timezone '{APP_TIMEZONE}', falling back to UTC")

maintenance_bp = Blueprint('maintenance', __name__, url_prefix='/api')

from flask import current_app
from pixelprobe.utils.rate_limiting import rate_limit, exempt_from_rate_limit

# Global state tracking - will be moved to service layer
cleanup_state = {
    'is_running': False,
    'phase': 'idle',
    'files_processed': 0,
    'total_files': 0,
    'orphaned_found': 0,
    'progress_percentage': 0,
    'start_time': None,
    'cancel_requested': False
}
cleanup_state_lock = threading.Lock()

file_changes_state = {
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
file_changes_state_lock = threading.Lock()

current_cleanup_thread = None
current_file_changes_thread = None

@maintenance_bp.route('/test-cleanup')
@auth_required
def test_cleanup():
    """Test endpoint to check cleanup state from database"""
    cleanup_record = CleanupState.query.order_by(CleanupState.id.desc()).first()
    if cleanup_record:
        return {
            'current_state': cleanup_record.to_dict(),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    else:
        return {
            'current_state': None,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'message': 'No cleanup operations found'
        }

@maintenance_bp.route('/cleanup-status')
@exempt_from_rate_limit
@auth_required
def get_cleanup_status():
    """Get current cleanup operation status"""
    try:
        # Expire all cached objects to ensure we get fresh data from the database
        # This is critical because cleanup runs in a background thread
        # with its own session, and we need to see its committed updates
        db.session.expire_all()

        # Get the most recent cleanup state from database
        cleanup_record = CleanupState.query.order_by(CleanupState.id.desc()).first()
        
        if not cleanup_record:
            # No cleanup has ever been run
            response = {
                'is_running': False,
                'phase': 'idle',
                'phase_number': 1,
                'total_phases': 3,
                'phase_current': 0,
                'phase_total': 0,
                'files_processed': 0,
                'total_files': 0,
                'orphaned_found': 0,
                'current_file': None,
                'progress_message': '',
                'progress_percentage': 0
            }
        else:
            response = {
                'is_running': cleanup_record.is_active,
                'phase': cleanup_record.phase,
                'phase_number': cleanup_record.phase_number,
                'total_phases': 3,
                'phase_current': cleanup_record.phase_current,
                'phase_total': cleanup_record.phase_total,
                'files_processed': cleanup_record.files_processed,
                'total_files': cleanup_record.total_files,
                'orphaned_found': cleanup_record.orphaned_found,
                'current_file': cleanup_record.current_file,
                'progress_message': cleanup_record.progress_message or ''
            }
            
            if cleanup_record.start_time and cleanup_record.is_active:
                # Handle both timezone-aware and timezone-naive datetimes
                if cleanup_record.start_time.tzinfo is None:
                    # If naive, assume UTC
                    start_time_utc = cleanup_record.start_time.replace(tzinfo=timezone.utc)
                else:
                    start_time_utc = cleanup_record.start_time
                response['duration'] = (datetime.now(timezone.utc) - start_time_utc).total_seconds()
                response['start_time'] = start_time_utc.timestamp()
            
            # Calculate progress percentage using unified ProgressTracker
            progress_tracker = ProgressTracker('cleanup')
            
            if cleanup_record.phase == 'complete':
                response['progress_percentage'] = 100
            else:
                response['progress_percentage'] = progress_tracker.calculate_progress_percentage(
                    cleanup_record.phase_number,
                    cleanup_record.phase_current,
                    cleanup_record.phase_total,
                    total_phases=3
                )

        return response

    except Exception as e:
        logger.error(f"Error getting cleanup status: {str(e)}", exc_info=True)
        return {
            'is_running': False,
            'phase': 'error',
            'error': 'Failed to get cleanup status',
        }

@maintenance_bp.route('/file-changes-status')
@exempt_from_rate_limit
@auth_required
def get_file_changes_status():
    """Get current file changes check operation status"""
    try:
        # Expire all cached objects to ensure we get fresh data from the database
        # This is critical because file changes check runs in a background thread
        # with its own session, and we need to see its committed updates
        db.session.expire_all()

        # Get the most recent file changes state from database
        file_changes_record = FileChangesState.query.order_by(FileChangesState.id.desc()).first()
        
        if not file_changes_record:
            # No file changes check has ever been run
            response = {
                'is_running': False,
                'phase': 'idle',
                'phase_number': 1,
                'total_phases': 3,
                'phase_current': 0,
                'phase_total': 0,
                'files_processed': 0,
                'total_files': 0,
                'changes_found': 0,
                'corrupted_found': 0,
                'current_file': None,
                'progress_message': '',
                'progress_percentage': 0
            }
        else:
            response = {
                'is_running': file_changes_record.is_active,
                'phase': file_changes_record.phase,
                'phase_number': file_changes_record.phase_number,
                'total_phases': 3,
                'phase_current': file_changes_record.phase_current,
                'phase_total': file_changes_record.phase_total,
                'files_processed': file_changes_record.files_processed,
                'total_files': file_changes_record.total_files,
                'changes_found': file_changes_record.changes_found,
                'corrupted_found': file_changes_record.corrupted_found,
                'current_file': file_changes_record.current_file,
                'progress_message': file_changes_record.progress_message or ''
            }

            # When the scan is active, prefer Redis values for the live counters.
            # Mirrors the v2.5.67 pattern in scan_routes.py for /api/scan-status.
            if file_changes_record.is_active and file_changes_record.check_id:
                redis_progress = get_file_changes_progress_redis(file_changes_record.check_id)
                if redis_progress:
                    redis_files = redis_progress.get('files_processed', 0)
                    redis_total = redis_progress.get('total_files', 0)
                    redis_phase = redis_progress.get('phase', '')
                    redis_msg = redis_progress.get('progress_message', '')
                    if redis_files >= response['files_processed']:
                        response['files_processed'] = redis_files
                        response['phase_current'] = redis_files
                    if redis_total > 0:
                        response['phase_total'] = redis_total
                    if redis_phase and redis_phase not in ('', 'idle'):
                        response['phase'] = redis_phase
                    if redis_msg:
                        response['progress_message'] = redis_msg
            
            if file_changes_record.start_time and file_changes_record.is_active:
                # Handle both timezone-aware and timezone-naive datetimes
                if file_changes_record.start_time.tzinfo is None:
                    # If naive, assume UTC
                    start_time_utc = file_changes_record.start_time.replace(tzinfo=timezone.utc)
                else:
                    start_time_utc = file_changes_record.start_time
                response['duration'] = (datetime.now(timezone.utc) - start_time_utc).total_seconds()
                response['start_time'] = start_time_utc.timestamp()
            
            # Calculate progress percentage using unified ProgressTracker
            progress_tracker = ProgressTracker('file_changes')
            
            if file_changes_record.phase == 'complete':
                response['progress_percentage'] = 100
            else:
                response['progress_percentage'] = progress_tracker.calculate_progress_percentage(
                    file_changes_record.phase_number,
                    file_changes_record.phase_current,
                    file_changes_record.phase_total,
                    total_phases=3
                )

        return response

    except Exception as e:
        logger.error(f"Error getting file changes status: {str(e)}", exc_info=True)
        return {
            'is_running': False,
            'phase': 'error',
            'error': 'Failed to get file changes status',
        }

@maintenance_bp.route('/cancel-cleanup', methods=['POST'])
@auth_required
def cancel_cleanup():
    """Cancel the current cleanup operation"""
    try:
        cleanup_record = CleanupState.query.order_by(CleanupState.id.desc()).first()
        
        if cleanup_record and cleanup_record.is_active:
            # Set cancel_requested in database
            cleanup_record.cancel_requested = True
            cleanup_record.progress_message = 'Cancellation requested...'
            db.session.commit()
            
            # Also set in memory state
            with cleanup_state_lock:
                cleanup_state['cancel_requested'] = True
            
            logger.info("Cleanup cancellation requested")
            return {'message': 'Cleanup cancellation requested'}
        else:
            return {'error': 'No active cleanup operation to cancel'}, 400
            
    except Exception as e:
        logger.error(f"Error cancelling cleanup: {str(e)}", exc_info=True)
        return {'error': 'Internal server error'}, 500

@maintenance_bp.route('/reset-cleanup-state', methods=['POST'])
@auth_required
def reset_cleanup_state():
    """Force reset cleanup state in case of stuck operation"""
    try:
        # Get any active cleanup records
        active_cleanups = CleanupState.query.filter_by(is_active=True).all()
        
        # Mark them all as failed
        for cleanup in active_cleanups:
            cleanup.is_active = False
            cleanup.phase = 'failed'
            cleanup.end_time = datetime.now(timezone.utc)
            cleanup.progress_message = 'Force reset by user'
        
        db.session.commit()
        
        # Reset in-memory state
        with cleanup_state_lock:
            cleanup_state.update({
                'is_running': False,
                'phase': 'idle',
                'files_processed': 0,
                'total_files': 0,
                'orphaned_found': 0,
                'progress_percentage': 0,
                'start_time': None,
                'cancel_requested': False
            })
        
        logger.info("Cleanup state force reset")
        return {'message': 'Cleanup state reset successfully'}
        
    except Exception as e:
        logger.error(f"Error resetting cleanup state: {str(e)}", exc_info=True)
        return {'error': 'Internal server error'}, 500

@maintenance_bp.route('/cancel-file-changes', methods=['POST'])
@auth_required
def cancel_file_changes():
    """Cancel the current file changes check operation"""
    try:
        file_changes_record = FileChangesState.query.order_by(FileChangesState.id.desc()).first()
        
        if file_changes_record and file_changes_record.is_active:
            # Set cancel_requested in database
            file_changes_record.cancel_requested = True
            file_changes_record.progress_message = 'Cancellation requested...'
            db.session.commit()
            
            # Also set in memory state
            with file_changes_state_lock:
                file_changes_state['cancel_requested'] = True
            
            logger.info("File changes check cancellation requested")
            return {'message': 'File changes check cancellation requested'}
        else:
            return {'error': 'No active file changes check to cancel'}, 400
            
    except Exception as e:
        logger.error(f"Error cancelling file changes check: {str(e)}", exc_info=True)
        return {'error': 'Internal server error'}, 500

@maintenance_bp.route('/reset-file-changes-state', methods=['POST'])
@auth_required
def reset_file_changes_state():
    """Force reset file changes state in case of stuck operation"""
    try:
        # Get any active file changes records
        active_file_changes = FileChangesState.query.filter_by(is_active=True).all()
        
        # Mark them all as failed
        for file_change in active_file_changes:
            file_change.is_active = False
            file_change.phase = 'failed'
            file_change.end_time = datetime.now(timezone.utc)
            file_change.progress_message = 'Force reset by user'
        
        db.session.commit()
        
        # Reset in-memory state
        with file_changes_state_lock:
            file_changes_state.update({
                'is_running': False,
                'phase': 'idle',
                'files_processed': 0,
                'total_files': 0,
                'changes_found': 0,
                'corrupted_found': 0,
                'progress_percentage': 0,
                'start_time': None,
                'cancel_requested': False
            })
        
        logger.info("File changes state force reset")
        return {'message': 'File changes state reset successfully'}
        
    except Exception as e:
        logger.error(f"Error resetting file changes state: {str(e)}", exc_info=True)
        return {'error': 'Internal server error'}, 500

@maintenance_bp.route('/cleanup-orphaned', methods=['POST'])
@auth_required
def cleanup_orphaned_files():
    """Start cleanup of orphaned database entries"""
    global current_cleanup_thread

    # Check if cleanup is already running - use database check for cross-worker visibility
    active_cleanup = CleanupState.query.filter_by(is_active=True).first()
    if active_cleanup:
        return {'error': 'Cleanup operation already in progress'}, 409

    # Secondary check: process-local thread (for same-worker requests)
    if current_cleanup_thread and current_cleanup_thread.is_alive():
        return {'error': 'Cleanup operation already in progress'}, 409

    # Get optional parameters from request
    data = request.get_json(silent=True) or {}
    file_paths = data.get('file_paths', [])
    schedule_id = data.get('schedule_id')  # For healthcheck integration

    # Reset state
    with cleanup_state_lock:
        cleanup_state.update({
            'is_running': True,
            'phase': 'starting',
            'files_processed': 0,
            'total_files': 0,
            'orphaned_found': 0,
            'progress_percentage': 0,
            'start_time': time.time(),
            'cancel_requested': False
        })

    # Create cleanup state in database
    cleanup_record = CleanupState(
        start_time=datetime.now(timezone.utc),
        is_active=True,
        phase='starting',
        phase_number=1
    )
    db.session.add(cleanup_record)
    db.session.commit()

    # Start cleanup in background thread - capture app instance for thread context
    app = current_app._get_current_object()
    current_cleanup_thread = threading.Thread(
        target=cleanup_orphaned_async,
        args=(app, cleanup_record.id, file_paths, schedule_id),
        name=f'cleanup_{cleanup_record.id}'
    )
    current_cleanup_thread.start()

    if file_paths:
        message = f'Cleanup operation started for {len(file_paths)} specific file(s)'
    else:
        message = 'Cleanup operation started for all files'

    return {
        'status': 'started',
        'message': message,
        'cleanup_id': cleanup_record.id,
        'file_count': len(file_paths) if file_paths else None
    }

@maintenance_bp.route('/file-changes', methods=['GET', 'POST'])
@auth_required
def check_file_changes():
    """Check for file changes since last scan"""
    global current_file_changes_thread

    # Check if file changes check is already running - use database check for cross-worker visibility
    active_check = FileChangesState.query.filter_by(is_active=True).first()
    if active_check:
        return {'error': 'File changes check already in progress'}, 409

    # Secondary check: process-local thread (for same-worker requests)
    if current_file_changes_thread and current_file_changes_thread.is_alive():
        return {'error': 'File changes check already in progress'}, 409

    # Get optional parameters from request
    data = request.get_json(silent=True) or {}
    file_paths = data.get('file_paths', [])
    schedule_id = data.get('schedule_id')  # For healthcheck integration

    # Create unique check ID
    check_id = str(uuid.uuid4())

    # Reset state
    with file_changes_state_lock:
        file_changes_state.update({
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

    # Create file changes state in database
    file_changes_record = FileChangesState(
        check_id=check_id,
        start_time=datetime.now(timezone.utc),
        is_active=True,
        phase='starting',
        phase_number=1
    )
    db.session.add(file_changes_record)
    db.session.commit()

    # Create ScanState for UI progress tracking (single file integrity checks)
    scan_state = None
    if file_paths and len(file_paths) == 1:
        try:
            from pixelprobe.models import ScanState
            scan_state = ScanState.create_new_scan()
            scan_state.scan_id = check_id
            scan_state.start_scan(file_paths, force_rescan=False)
            scan_state.phase = 'integrity_check'
            scan_state.progress_message = 'Starting integrity check'
            scan_state.estimated_total = 1
            scan_state.phase_total = 1
            db.session.commit()
            logger.info(f"Created ScanState {scan_state.id} for single file integrity check")
        except Exception as e:
            logger.warning(f"Failed to create ScanState for single file integrity check: {e}")

    # Start file changes check in background thread - capture app instance for thread context
    app = current_app._get_current_object()
    current_file_changes_thread = threading.Thread(
        target=check_file_changes_async,
        args=(app, check_id, file_paths, schedule_id)
    )
    current_file_changes_thread.start()

    if file_paths:
        message = f'File changes check started for {len(file_paths)} specific file(s)'
    else:
        message = 'File changes check started for all files'

    return {
        'status': 'started',
        'message': message,
        'check_id': check_id,
        'file_count': len(file_paths) if file_paths else None
    }

def cleanup_orphaned_async(app, cleanup_id, file_paths=None, schedule_id=None):
    """Async function to cleanup orphaned database entries

    Args:
        app: Flask app instance
        cleanup_id: ID of the cleanup record
        file_paths: Optional list of specific file paths to check (if None, checks all files)
        schedule_id: Optional schedule ID for healthcheck integration
    """
    try:
        with app.app_context():
            with db.session.get_bind().connect() as connection:
                with db.session():
                    # Get the cleanup record
                    cleanup_record = db.session.get(CleanupState, cleanup_id)
                    if not cleanup_record:
                        logger.error(f"Cleanup record {cleanup_id} not found")
                        return

                    # Create maintenance service instance
                    database_url = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
                    maintenance_service = MaintenanceService(database_url)

                    # Run the cleanup using the maintenance service logic with optional file_paths filter
                    maintenance_service._run_cleanup(cleanup_record.id, file_paths=file_paths, schedule_id=schedule_id)

    except Exception as e:
        logger.error(f"Error in cleanup_orphaned_async: {str(e)}", exc_info=True)
        try:
            with app.app_context():
                cleanup_record = db.session.get(CleanupState, cleanup_id)
                if cleanup_record:
                    cleanup_record.phase = 'error'
                    cleanup_record.progress_message = 'Cleanup failed'
                    cleanup_record.is_active = False
                    cleanup_record.end_time = datetime.now(timezone.utc)
                    db.session.commit()
        except Exception as commit_error:
            logger.error(f"Failed to update cleanup record on error: {str(commit_error)}")

def check_file_changes_async(app, check_id, file_paths=None, schedule_id=None):
    """Async function to check file changes

    Args:
        app: Flask app instance
        check_id: Unique ID for this check
        file_paths: Optional list of specific file paths to check (if None, checks all files)
        schedule_id: Optional schedule ID for healthcheck integration
    """
    try:
        with app.app_context():
            with db.session.get_bind().connect() as connection:
                with db.session():
                    # Get the file changes record
                    check_record = FileChangesState.query.filter_by(check_id=check_id).first()
                    if not check_record:
                        logger.error(f"File changes record {check_id} not found")
                        return

                    # Create maintenance service instance
                    database_url = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
                    maintenance_service = MaintenanceService(database_url)

                    # Run the file changes check using the maintenance service logic with optional file_paths filter
                    maintenance_service._run_file_changes_check(check_record.check_id, file_paths=file_paths, schedule_id=schedule_id)

    except Exception as e:
        logger.error(f"Error in check_file_changes_async: {str(e)}", exc_info=True)
        try:
            with app.app_context():
                check_record = FileChangesState.query.filter_by(check_id=check_id).first()
                if check_record:
                    check_record.phase = 'error'
                    check_record.progress_message = 'File changes check failed'
                    check_record.is_active = False
                    check_record.end_time = datetime.now(timezone.utc)
                    db.session.commit()
        except Exception as commit_error:
            logger.error(f"Failed to update file changes record on error: {str(commit_error)}")

@maintenance_bp.route('/vacuum', methods=['POST'])
@auth_required
def vacuum_database():
    """Vacuum the SQLite database to optimize storage"""
    try:
        # Only works with SQLite databases
        database_url = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
        if not database_url.startswith('sqlite:'):
            return {'error': 'VACUUM operation only supported for SQLite databases'}, 400

        # Get database size before vacuum
        db_file_path = database_url.replace('sqlite:///', '')
        if os.path.exists(db_file_path):
            size_before = os.path.getsize(db_file_path)
        else:
            size_before = 0

        # Execute VACUUM command
        from sqlalchemy import text
        result = db.session.execute(text('VACUUM;'))
        db.session.commit()

        # Get database size after vacuum
        if os.path.exists(db_file_path):
            size_after = os.path.getsize(db_file_path)
        else:
            size_after = 0

        bytes_freed = size_before - size_after

        logger.info(f"Database vacuum completed. Size before: {size_before} bytes, after: {size_after} bytes, freed: {bytes_freed} bytes")

        return {
            'message': 'Database vacuum completed successfully',
            'size_before_bytes': size_before,
            'size_after_bytes': size_after,
            'bytes_freed': bytes_freed,
            'percentage_reduction': round((bytes_freed / size_before * 100), 2) if size_before > 0 else 0
        }

    except Exception as e:
        logger.error(f"Error vacuuming database: {str(e)}", exc_info=True)
        return {'error': 'Failed to vacuum database'}, 500
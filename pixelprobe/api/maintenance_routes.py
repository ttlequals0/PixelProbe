from flask import Blueprint, request, jsonify
import os
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
import pytz

from models import db, ScanResult, CleanupState, FileChangesState
from media_checker import PixelProbe
from utils import ProgressTracker
from pixelprobe.services.maintenance_service import MaintenanceService

logger = logging.getLogger(__name__)

# Get timezone from environment variable, default to UTC
APP_TIMEZONE = os.environ.get('TZ', 'UTC')
try:
    tz = pytz.timezone(APP_TIMEZONE)
except pytz.exceptions.UnknownTimeZoneError:
    tz = pytz.UTC
    logger.warning(f"Unknown timezone '{APP_TIMEZONE}', falling back to UTC")

maintenance_bp = Blueprint('maintenance', __name__, url_prefix='/api')

# Import limiter from main app
from flask import current_app
from functools import wraps

# Create rate limit decorators that work with Flask-Limiter
def rate_limit(limit_string):
    """Decorator to apply rate limits using the app's limiter"""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            # Get the limiter from the current app
            limiter = current_app.extensions.get('flask-limiter')
            if limiter:
                # Apply the rate limit dynamically
                limited_func = limiter.limit(limit_string, exempt_when=lambda: False)(f)
                return limited_func(*args, **kwargs)
            else:
                # If no limiter, just call the function
                return f(*args, **kwargs)
        return wrapped
    return decorator

def exempt_from_rate_limit(f):
    """Decorator to exempt a function from rate limiting"""
    @wraps(f)
    def wrapped(*args, **kwargs):
        # Get the limiter from the current app
        limiter = current_app.extensions.get('flask-limiter')
        if limiter:
            # Apply exemption dynamically
            exempt_func = limiter.exempt(f)
            return exempt_func(*args, **kwargs)
        else:
            # If no limiter, just call the function
            return f(*args, **kwargs)
    return wrapped

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
def test_cleanup():
    """Test endpoint to check cleanup state from database"""
    cleanup_record = CleanupState.query.order_by(CleanupState.id.desc()).first()
    if cleanup_record:
        return jsonify({
            'current_state': cleanup_record.to_dict(),
            'timestamp': datetime.now().isoformat()
        })
    else:
        return jsonify({
            'current_state': None,
            'timestamp': datetime.now().isoformat(),
            'message': 'No cleanup operations found'
        })

@maintenance_bp.route('/cleanup-status')
@exempt_from_rate_limit
def get_cleanup_status():
    """Get current cleanup orphans operation status"""
    try:
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
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error getting cleanup status: {str(e)}")
        return jsonify({
            'is_running': False,
            'phase': 'error',
            'error': str(e)
        })

@maintenance_bp.route('/file-changes-status')
@exempt_from_rate_limit
def get_file_changes_status():
    """Get current file changes check operation status"""
    try:
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
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error getting file changes status: {str(e)}")
        return jsonify({
            'is_running': False,
            'phase': 'error',
            'error': str(e)
        })

@maintenance_bp.route('/cancel-cleanup', methods=['POST'])
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
            return jsonify({'message': 'Cleanup cancellation requested'})
        else:
            return jsonify({'error': 'No active cleanup operation to cancel'}), 400
            
    except Exception as e:
        logger.error(f"Error cancelling cleanup: {str(e)}")
        return jsonify({'error': str(e)}), 500

@maintenance_bp.route('/reset-cleanup-state', methods=['POST'])
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
        return jsonify({'message': 'Cleanup state reset successfully'})
        
    except Exception as e:
        logger.error(f"Error resetting cleanup state: {str(e)}")
        return jsonify({'error': str(e)}), 500

@maintenance_bp.route('/cancel-file-changes', methods=['POST'])
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
            return jsonify({'message': 'File changes check cancellation requested'})
        else:
            return jsonify({'error': 'No active file changes check to cancel'}), 400
            
    except Exception as e:
        logger.error(f"Error cancelling file changes check: {str(e)}")
        return jsonify({'error': str(e)}), 500

@maintenance_bp.route('/reset-file-changes-state', methods=['POST'])
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
        return jsonify({'message': 'File changes state reset successfully'})
        
    except Exception as e:
        logger.error(f"Error resetting file changes state: {str(e)}")
        return jsonify({'error': str(e)}), 500

@maintenance_bp.route('/cleanup-orphaned', methods=['POST'])
def cleanup_orphaned_files():
    """Start cleanup of orphaned database entries"""
    global current_cleanup_thread
    
    # Check if cleanup is already running
    if current_cleanup_thread and current_cleanup_thread.is_alive():
        return jsonify({'error': 'Cleanup operation already in progress'}), 409
    
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
        args=(app, cleanup_record.id,)
    )
    current_cleanup_thread.start()
    
    return jsonify({
        'status': 'started',
        'message': 'Cleanup operation started',
        'cleanup_id': cleanup_record.id
    })

@maintenance_bp.route('/file-changes', methods=['GET', 'POST'])
def check_file_changes():
    """Check for file changes since last scan"""
    global current_file_changes_thread
    
    # Check if file changes check is already running
    if current_file_changes_thread and current_file_changes_thread.is_alive():
        return jsonify({'error': 'File changes check already in progress'}), 409
    
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
    
    # Start file changes check in background thread - capture app instance for thread context
    app = current_app._get_current_object()
    current_file_changes_thread = threading.Thread(
        target=check_file_changes_async,
        args=(app, check_id,)
    )
    current_file_changes_thread.start()
    
    return jsonify({
        'status': 'started',
        'message': 'File changes check started',
        'check_id': check_id
    })

def cleanup_orphaned_async(app, cleanup_id):
    """Async function to cleanup orphaned database entries"""
    try:
        with app.app_context():
            with db.session.get_bind().connect() as connection:
                with db.session():
                    # Get the cleanup record
                    cleanup_record = CleanupState.query.get(cleanup_id)
                    if not cleanup_record:
                        logger.error(f"Cleanup record {cleanup_id} not found")
                        return
                    
                    # Create maintenance service instance
                    database_url = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
                    maintenance_service = MaintenanceService(database_url)
                    
                    # Run the cleanup using the maintenance service logic
                    maintenance_service._run_cleanup(cleanup_record.id)
                
    except Exception as e:
        logger.error(f"Error in cleanup_orphaned_async: {str(e)}")
        try:
            with app.app_context():
                cleanup_record = CleanupState.query.get(cleanup_id)
                if cleanup_record:
                    cleanup_record.phase = 'error'
                    cleanup_record.progress_message = f'Error: {str(e)}'
                    cleanup_record.is_active = False
                    cleanup_record.end_time = datetime.now(timezone.utc)
                    db.session.commit()
        except Exception as commit_error:
            logger.error(f"Failed to update cleanup record on error: {str(commit_error)}")

def check_file_changes_async(app, check_id):
    """Async function to check file changes"""
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
                    
                    # Run the file changes check using the maintenance service logic
                    maintenance_service._run_file_changes_check(check_record.check_id)
                
    except Exception as e:
        logger.error(f"Error in check_file_changes_async: {str(e)}")
        try:
            with app.app_context():
                check_record = FileChangesState.query.filter_by(check_id=check_id).first()
                if check_record:
                    check_record.phase = 'error'
                    check_record.progress_message = f'Error: {str(e)}'
                    check_record.is_active = False
                    check_record.end_time = datetime.now(timezone.utc)
                    db.session.commit()
        except Exception as commit_error:
            logger.error(f"Failed to update file changes record on error: {str(commit_error)}")

@maintenance_bp.route('/vacuum', methods=['POST'])
def vacuum_database():
    """Vacuum the SQLite database to optimize storage"""
    try:
        # Only works with SQLite databases
        database_url = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
        if not database_url.startswith('sqlite:'):
            return jsonify({'error': 'VACUUM operation only supported for SQLite databases'}), 400
        
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
        
        return jsonify({
            'message': 'Database vacuum completed successfully',
            'size_before_bytes': size_before,
            'size_after_bytes': size_after,
            'bytes_freed': bytes_freed,
            'percentage_reduction': round((bytes_freed / size_before * 100), 2) if size_before > 0 else 0
        })
        
    except Exception as e:
        logger.error(f"Error vacuuming database: {str(e)}")
        return jsonify({'error': f'Failed to vacuum database: {str(e)}'}), 500
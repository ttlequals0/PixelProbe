from flask import Blueprint, request, jsonify, current_app
import os
import threading
import logging
from datetime import datetime, timezone, timedelta
from pixelprobe.utils.timezone import from_utc_to_configured

from media_checker import PixelProbe, load_exclusions
from models import db, ScanResult, ScanState
from version import __version__
from auth import auth_required

from pixelprobe.utils.security import (
    validate_file_path, validate_directory_path,
    PathTraversalError, AuditLogger, validate_json_input
)
# Remove direct limiter imports as we'll use decorators

logger = logging.getLogger(__name__)


def check_celery_available():
    """Check if Celery is available and broker is reachable"""
    celery_enabled = current_app.config.get('CELERY_BROKER_URL') and hasattr(current_app, 'celery')
    
    if celery_enabled:
        # Test Celery broker connection before using
        try:
            current_app.celery.control.ping(timeout=1.0)
        except Exception as e:
            logger.warning(f"Celery broker connection failed: {e}. Falling back to direct scan service.")
            celery_enabled = False
    
    return celery_enabled


# Timezone handling via utility module

scan_bp = Blueprint('scan', __name__, url_prefix='/api')

# Import limiter from main app
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

def is_scan_running():
    """Check if a scan is currently running (thread or Celery)"""
    # Check thread-based scans
    if current_app.scan_service.is_scan_running():
        return True
    
    # Check database for active Celery-based scans
    try:
        active_scan = ScanState.query.filter_by(is_active=True).first()
        if active_scan and active_scan.phase not in ['idle', 'completed', 'error', 'crashed', 'cancelled']:
            # Check if scan is stale (no progress update)
            from datetime import datetime, timezone, timedelta
            
            # Use last_update if available, otherwise use start_time
            check_time = active_scan.last_update or active_scan.start_time
            
            if check_time:
                # Ensure timezone-aware
                if check_time.tzinfo is None:
                    check_time = check_time.replace(tzinfo=timezone.utc)
                
                now = datetime.now(timezone.utc)
                time_since_update = now - check_time
                
                # Check for stuck scan based on phase
                stuck_threshold = timedelta(minutes=10)  # Default 10 minutes
                
                # Adding phase can get stuck - use shorter timeout
                if active_scan.phase == 'adding':
                    stuck_threshold = timedelta(minutes=5)
                    
                    # Also check if files_processed hasn't changed
                    if hasattr(active_scan, '_last_files_processed'):
                        if active_scan.files_processed == active_scan._last_files_processed:
                            logger.warning(f"Scan stuck in adding phase at {active_scan.files_processed} files")
                            stuck_threshold = timedelta(minutes=2)  # Even shorter if no progress
                
                if time_since_update > stuck_threshold:
                    logger.warning(f"Scan {active_scan.scan_id} appears stuck in phase '{active_scan.phase}' (no update for {time_since_update})")
                    logger.warning(f"Files processed: {active_scan.files_processed}/{active_scan.estimated_total}")
                    
                    # But first check if Celery task is actually still running before marking as crashed
                    if active_scan.celery_task_id and check_celery_available():
                        try:
                            from celery.result import AsyncResult
                            result = AsyncResult(active_scan.celery_task_id, app=current_app.celery)
                            # Check actual task state
                            if result.state in ['PENDING', 'STARTED', 'RETRY', 'PROGRESS']:
                                logger.info(f"Celery task {active_scan.celery_task_id} still active with state: {result.state} - scan is running despite no recent update")
                                return True  # Task is still running, just slow
                            else:
                                logger.warning(f"Celery task {active_scan.celery_task_id} is not active (state: {result.state})")
                        except Exception as e:
                            logger.warning(f"Could not check Celery task status: {e}")
                    
                    # Mark as crashed and allow new scan
                    active_scan.is_active = False
                    active_scan.phase = 'crashed'
                    active_scan.error_message = f'Scan stuck in {active_scan.phase} phase - no progress for {time_since_update}'
                    db.session.commit()
                    return False
            
            # Also verify if Celery task is still running for non-stuck scans
            if active_scan.celery_task_id and check_celery_available():
                try:
                    from celery.result import AsyncResult
                    result = AsyncResult(active_scan.celery_task_id, app=current_app.celery)
                    # Check actual task state
                    if result.state in ['PENDING', 'STARTED', 'RETRY', 'PROGRESS']:
                        return True
                    elif result.state in ['SUCCESS', 'FAILURE', 'REVOKED']:
                        # Task completed but scan state not updated - clean up
                        logger.warning(f"Celery task {active_scan.celery_task_id} in state {result.state} but scan still active - cleaning up")
                        active_scan.is_active = False
                        active_scan.phase = 'completed' if result.state == 'SUCCESS' else 'error'
                        db.session.commit()
                        return False
                except Exception as e:
                    logger.warning(f"Could not check Celery task status: {e}")
                    # If we can't check the task, don't block forever
                    return False
            
            # If no Celery task ID, it's likely a direct scan - check if thread is alive
            if not active_scan.celery_task_id:
                return True  # Let thread-based check handle it
            
            # Celery task doesn't exist or can't be checked - likely orphaned
            logger.warning(f"Scan {active_scan.scan_id} has no valid Celery task - marking as crashed")
            active_scan.is_active = False
            active_scan.phase = 'crashed'
            active_scan.error_message = 'Celery task lost - worker may have restarted'
            db.session.commit()
            return False
    except Exception as e:
        logger.error(f"Error checking scan state: {e}")
    
    return False

@scan_bp.route('/scan-results')
@auth_required
def get_scan_results():
    """Get paginated scan results with optional filters"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 100, type=int)
    scan_status = request.args.get('scan_status', 'all')
    is_corrupted = request.args.get('is_corrupted', 'all')
    has_warnings = request.args.get('has_warnings', 'all')
    search_query = request.args.get('search', '').strip()
    sort_field = request.args.get('sort_field', 'scan_date')
    sort_order = request.args.get('sort_order', 'desc')
    
    # Build query
    query = ScanResult.query
    
    # Apply search filter
    if search_query:
        query = query.filter(ScanResult.file_path.ilike(f'%{search_query}%'))
    
    # Apply status filter
    if scan_status != 'all':
        query = query.filter_by(scan_status=scan_status)
    
    # Apply corruption filter
    if is_corrupted == 'true':
        query = query.filter_by(is_corrupted=True).filter_by(marked_as_good=False)
    elif is_corrupted == 'false':
        query = query.filter(
            (ScanResult.is_corrupted == False) | 
            (ScanResult.marked_as_good == True)
        )
    
    # Apply warnings filter
    if has_warnings == 'true':
        query = query.filter(
            (ScanResult.has_warnings == True) & 
            (ScanResult.marked_as_good == False) &
            (ScanResult.is_corrupted == False)  # Exclude corrupted files from warnings filter
        )
    
    # Apply sorting
    # Map frontend field names to model attributes
    field_mapping = {
        'scan_date': ScanResult.scan_date,
        'file_path': ScanResult.file_path,
        'file_size': ScanResult.file_size,
        'file_type': ScanResult.file_type,
        'scan_status': ScanResult.scan_status,
        'status': ScanResult.is_corrupted,  # Frontend uses "status" for corruption status
        'is_corrupted': ScanResult.is_corrupted,
        'marked_as_good': ScanResult.marked_as_good,
        'scan_tool': ScanResult.scan_tool,
        'corruption_details': ScanResult.corruption_details,
        'discovered_date': ScanResult.discovered_date,
        'last_modified': ScanResult.last_modified
    }
    
    if sort_field in field_mapping:
        field_attr = field_mapping[sort_field]
        if sort_order.lower() == 'asc':
            query = query.order_by(field_attr.asc())
        else:
            query = query.order_by(field_attr.desc())
    else:
        # Default sorting
        query = query.order_by(ScanResult.scan_date.desc())
    
    # Paginate - handle -1 as "show all"
    if per_page == -1:
        # Get all results without pagination
        all_results = query.all()
        # Create a mock pagination object
        class MockPagination:
            def __init__(self, items, total):
                self.items = items
                self.total = total
                self.pages = 1
        pagination = MockPagination(all_results, len(all_results))
    else:
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    # Build response
    results = []
    for result in pagination.items:
        result_dict = result.to_dict()
        
        # Convert timestamps to configured timezone for display
        if result.scan_date:
            display_dt = from_utc_to_configured(result.scan_date)
            result_dict['scan_date'] = display_dt.isoformat() if display_dt else None
        
        if result.discovered_date:
            display_dt = from_utc_to_configured(result.discovered_date)
            result_dict['discovered_date'] = display_dt.isoformat() if display_dt else None
        
        if result.creation_date:
            display_dt = from_utc_to_configured(result.creation_date)
            result_dict['creation_date'] = display_dt.isoformat() if display_dt else None
        
        if result.last_modified:
            display_dt = from_utc_to_configured(result.last_modified)
            result_dict['last_modified'] = display_dt.isoformat() if display_dt else None
        
        # Add file_name for frontend convenience
        result_dict['file_name'] = os.path.basename(result.file_path) if result.file_path else ''
        
        results.append(result_dict)
    
    return jsonify({
        'results': results,
        'total': pagination.total,
        'page': page,
        'per_page': per_page,
        'pages': pagination.pages
    })

@scan_bp.route('/scan-results/<int:result_id>')
@auth_required
def get_scan_result(result_id):
    """Get a single scan result by ID"""
    result = ScanResult.query.get_or_404(result_id)
    result_dict = result.to_dict()
    
    # Convert timestamps to configured timezone for display
    if result.scan_date:
        display_dt = from_utc_to_configured(result.scan_date)
        result_dict['scan_date'] = display_dt.isoformat() if display_dt else None
    
    if result.discovered_date:
        display_dt = from_utc_to_configured(result.discovered_date)
        result_dict['discovered_date'] = display_dt.isoformat() if display_dt else None
    
    if result.creation_date:
        display_dt = from_utc_to_configured(result.creation_date)
        result_dict['creation_date'] = display_dt.isoformat() if display_dt else None
    
    if result.last_modified:
        display_dt = from_utc_to_configured(result.last_modified)
        result_dict['last_modified'] = display_dt.isoformat() if display_dt else None
    
    return jsonify(result_dict)

@scan_bp.route('/scan-file', methods=['POST'])
@rate_limit("5 per minute")
@auth_required
@validate_json_input({
    'file_path': {'required': True, 'type': str, 'max_length': 1000}
})
def scan_file():
    """Scan a single file for corruption"""
    data = request.get_json()
    file_path = data['file_path']
    
    # Validate file path for security
    try:
        validated_path = validate_file_path(file_path)
        AuditLogger.log_action('scan_file', {'file_path': validated_path})
    except PathTraversalError as e:
        AuditLogger.log_security_event('path_traversal_attempt', str(e), 'warning')
        return jsonify({'error': 'Invalid file path'}), 400
    
    # P1 Implementation: Use Celery task queue for single file scans
    try:
        # Check if Celery is available and test connection
        celery_enabled = check_celery_available()
        
        if celery_enabled:
            # Use Celery task queue
            from pixelprobe.tasks import scan_media_task, ui_progress_update_task
            from uuid import uuid4

            # Generate scan ID
            scan_id = str(uuid4())

            # Create ScanState record for UI progress tracking
            scan_state = ScanState.create_new_scan()
            scan_state.scan_id = scan_id
            scan_state.start_scan([validated_path], force_rescan=True)

            # Start UI progress worker for single file scan
            try:
                ui_progress_update_task.delay(scan_id)
                logger.info(f"Started UI progress worker for scan {scan_id}")
            except Exception as e:
                logger.warning(f"Failed to start UI progress worker: {e}")

            # Queue the single file scan task
            task = scan_media_task.delay(
                scan_id=scan_id,
                paths=[validated_path],
                scan_type='single',
                force_rescan=True
            )

            logger.info(f"Queued single file scan task {task.id} for {validated_path}")

            return jsonify({
                'status': 'queued',
                'scan_id': scan_id,
                'task_id': task.id,
                'file_path': validated_path,
                'message': 'Single file scan queued successfully using Celery task queue',
                'celery_enabled': True
            })
        else:
            # Fallback to direct scan service
            result = current_app.scan_service.scan_single_file(validated_path, force_rescan=True)
            result['celery_enabled'] = False
            return jsonify(result)
            
    except RuntimeError as e:
        error_msg = str(e)
        # Provide more context for common errors
        if 'already running' in error_msg.lower():
            error_msg = f'{error_msg}. Use /api/scan-status to check progress or /api/cancel-scan to stop the current scan.'
        return jsonify({'error': error_msg, 'suggestion': 'Check /api/scan-status for current scan progress'}), 409
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404

@scan_bp.route('/scan', methods=['POST', 'OPTIONS'])  # Main scan endpoint
@rate_limit("2 per minute")
@auth_required
@validate_json_input({
    'force_rescan': {'required': False, 'type': bool},
    'directories': {'required': False, 'type': list}
})
def scan():
    """Start scanning all media files in configured directories"""
    if request.method == 'OPTIONS':
        return '', 200

    # ATOMIC: Use database row-level locking to prevent race conditions
    # Lock the scan state row for update to ensure only one scan can start
    try:
        scan_state = db.session.query(ScanState).with_for_update(nowait=True).first()
        if not scan_state:
            scan_state = ScanState()
            db.session.add(scan_state)
            db.session.flush()
            scan_state = db.session.query(ScanState).with_for_update(nowait=True).first()

        # Check if a scan is already running while we hold the lock
        if scan_state.is_active and scan_state.phase not in ['idle', 'completed', 'error', 'crashed', 'cancelled']:
            phase_info = f" (Phase: {scan_state.phase}, Files processed: {scan_state.files_processed})"
            db.session.rollback()
            return jsonify({
                'error': f'A scan is already in progress{phase_info}. Please wait for it to complete or use /api/cancel-scan to stop it.'
            }), 409

        # If we got here, no scan is running - we can proceed with the lock held
        # Mark as starting immediately while we hold the lock
        scan_state.is_active = True
        scan_state.phase = 'initializing'
        db.session.commit()

    except Exception as lock_error:
        # If we can't acquire the lock, another scan is starting
        db.session.rollback()
        logger.warning(f"Could not acquire scan lock: {lock_error}")
        return jsonify({
            'error': 'A scan is already starting. Please wait a moment and try again.'
        }), 409

    # Get scan configuration
    data = request.get_json() or {}
    force_rescan = data.get('force_rescan', False)
    scan_dirs = data.get('directories', [])
    
    # If no directories provided, use configured ones
    if not scan_dirs:
        from models import ScanConfiguration
        configs = ScanConfiguration.query.filter_by(is_active=True).all()
        scan_dirs = [config.path for config in configs]
        
        # If no database config, fall back to environment variable
        if not scan_dirs:
            scan_paths_env = os.environ.get('SCAN_PATHS', '')
            scan_dirs = [path.strip() for path in scan_paths_env.split(',') if path.strip()]
            logger.info(f"Using SCAN_PATHS from environment: {scan_dirs}")
    
    if not scan_dirs:
        return jsonify({'error': 'No directories configured for scanning. Set SCAN_PATHS environment variable or configure paths in the admin interface.'}), 400
    
    # Validate directories
    validated_dirs = []
    for dir_path in scan_dirs:
        try:
            validated_path = validate_directory_path(dir_path)
            validated_dirs.append(validated_path)
        except Exception as e:
            AuditLogger.log_security_event('invalid_scan_directory', str(e), 'warning')
            return jsonify({'error': f'Invalid directory path: {dir_path}'}), 400
    
    AuditLogger.log_action('scan_all', {'directories': validated_dirs, 'force_rescan': force_rescan})
    
    # P1 Implementation: Use Celery task queue instead of direct scan service
    try:
        # Check if Celery is available
        celery_enabled = check_celery_available()
        
        if celery_enabled:
            # Use Celery task queue
            from pixelprobe.tasks import scan_media_task
            from uuid import uuid4
            
            # Generate scan ID
            scan_id = str(uuid4())
            
            # Queue the scan task
            task = scan_media_task.delay(
                scan_id=scan_id,
                paths=validated_dirs,
                scan_type='full',
                force_rescan=force_rescan
            )
            
            logger.info(f"Queued full scan task {task.id} for scan_id {scan_id}")
            
            return jsonify({
                'status': 'queued',
                'scan_id': scan_id,
                'task_id': task.id,
                'message': 'Scan queued successfully using Celery task queue',
                'celery_enabled': True
            })
        else:
            # Fallback to direct scan service (backward compatibility)
            logger.info("Celery not available, using direct scan service")
            from config import Config
            result = current_app.scan_service.scan_directories(
                validated_dirs,
                force_rescan=force_rescan,
                num_workers=Config.MAX_WORKERS  # Use configured MAX_WORKERS instead of hardcoded 1
            )
            result['celery_enabled'] = False
            return jsonify(result)
            
    except RuntimeError as e:
        error_msg = str(e)
        # Provide more context for common errors
        if 'already running' in error_msg.lower():
            error_msg = f'{error_msg}. Use /api/scan-status to check progress or /api/cancel-scan to stop the current scan.'
        return jsonify({'error': error_msg, 'suggestion': 'Check /api/scan-status for current scan progress'}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

@scan_bp.route('/scan-status')
@exempt_from_rate_limit
@auth_required
def get_scan_status():
    """Get current scan status and progress"""
    # Get progress from scan service
    service_status = current_app.scan_service.get_scan_progress()
    
    # Force fresh database read - bypass session cache for Celery workers
    # This ensures we see updates made by Celery worker processes
    try:
        # CRITICAL: For Celery workers in separate processes, we need to close and recreate session
        # to ensure we see committed changes from other processes
        db.session.close()
        db.session.remove()  # Remove session completely to force new connection

        # First try to get active scan with fresh session
        scan_state = db.session.query(ScanState).filter_by(is_active=True).first()
        if scan_state:
            # Force refresh from database to get latest state
            db.session.refresh(scan_state)
        else:
            # No active scan, get the most recent COMPLETED scan for status display
            # IMPORTANT: Only show completed scans to avoid showing partial/interrupted scans
            scan_state = db.session.query(ScanState).filter_by(phase='completed').order_by(ScanState.id.desc()).first()
            if not scan_state:
                # If no completed scan, get the most recent one regardless of phase
                scan_state = db.session.query(ScanState).order_by(ScanState.id.desc()).first()

            if scan_state:
                db.session.refresh(scan_state)
            else:
                # No scan states at all, create initial one in idle state
                scan_state = ScanState()
                scan_state.phase = 'idle'
                scan_state.is_active = False
                db.session.add(scan_state)
                db.session.commit()
    except Exception as e:
        logger.warning(f"Could not get fresh scan state: {e}")
        scan_state = ScanState.get_or_create()
    
    state_dict = scan_state.to_dict()
    
    # Debug logging - changed to INFO for visibility in production logs
    logger.info(f"API scan-status: scan_id={scan_state.id}, phase={scan_state.phase}, "
                f"is_active={scan_state.is_active}, files_processed={scan_state.files_processed}, "
                f"estimated_total={scan_state.estimated_total}, current_file={scan_state.current_file}, "
                f"start_time={scan_state.start_time}")
    
    # Prioritize database values when available, fall back to service values
    is_running = current_app.scan_service.is_scan_running()
    logger.debug(f"Service is_running: {is_running}")
    logger.debug(f"Service status: {service_status}")
    logger.debug(f"Database state_dict phase: {state_dict.get('phase', 'idle')}")
    
    # Prioritize database state when scan is active, fall back to service values
    current_phase = state_dict.get('phase', 'idle')
    
    # Use database values primarily, with service as fallback
    current_progress = state_dict.get('files_processed', service_status.get('current', 0))
    total_progress = state_dict.get('estimated_total', service_status.get('total', 0))
    
    # Determine status based on phase and progress
    if is_running:
        if current_phase in ['discovering', 'adding', 'scanning']:
            status_value = current_phase
        else:
            status_value = service_status.get('status', 'scanning')
    else:
        status_value = 'completed' if current_phase == 'completed' else 'idle'
    
    # Map phases to frontend-expected phase numbers with proper progress calculation
    phase_number = 1
    total_phases = 3
    progress_message = ""
    phase_current = 0
    phase_total = 0
    
    if current_phase == 'discovering':
        phase_number = 1
        # Use the actual progress message from database if available
        db_progress_msg = state_dict.get('progress_message')
        progress_message = db_progress_msg if db_progress_msg else "Phase 1 of 3: Discovering files..."
        # For discovery, we don't know total files yet, so show indeterminate progress
        phase_current = 0
        phase_total = 0  # Will show base phase progress (0-33%)
        
    elif current_phase == 'adding':
        phase_number = 2  
        # Use the actual progress message from database if available
        db_progress_msg = state_dict.get('progress_message')
        progress_message = db_progress_msg if db_progress_msg else f"Phase 2 of 3: Adding files to database - {current_progress} of {total_progress:,} files"
        # Use current/total from database for adding phase
        phase_current = current_progress
        phase_total = total_progress
        
    elif current_phase == 'scanning':
        phase_number = 3
        # Generate fresh progress message based on current data
        current_file = state_dict.get('current_file', '')
        if current_file:
            # Extract just the filename for display
            import os
            filename = os.path.basename(current_file)
            # Generate fresh progress message with current data
            from utils import ProgressTracker
            progress_tracker = ProgressTracker('scan')
            # Use the actual scan start time if available
            if state_dict.get('start_time'):
                try:
                    start_time_str = state_dict['start_time']
                    if isinstance(start_time_str, str):
                        start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                    else:
                        start_time = start_time_str
                    if start_time.tzinfo is None:
                        start_time = start_time.replace(tzinfo=timezone.utc)
                    # Set the actual scan start time
                    import time
                    progress_tracker.start_time = start_time.timestamp()
                except:
                    pass  # Use default if parsing fails
            progress_message = progress_tracker.get_progress_message(
                'Phase 3 of 3: Scanning files',
                current_progress,
                total_progress,
                filename
            )
        else:
            progress_message = f"Phase 3 of 3: Scanning files - {current_progress} of {total_progress:,} files"
        # Use current/total from database for scanning phase  
        phase_current = current_progress
        phase_total = total_progress
        
    elif current_phase == 'completed':
        phase_number = 3
        progress_message = "Scan completed"
        phase_current = total_progress
        phase_total = total_progress
        
    # Check if scan is stuck (no progress for extended period)
    is_stuck_scan = False
    # Instead of time-based detection, we should check if progress has stalled
    # This will be handled by the frontend stuck detection logic
    
    # Calculate ETA if scan is running and we have progress
    eta = None
    files_per_second = 0
    logger.debug(f"ETA calculation: is_running={is_running}, start_time={state_dict.get('start_time')}, "
                 f"current={current_progress}, total={total_progress}")
    
    if is_running and state_dict.get('start_time') and current_progress > 0 and not is_stuck_scan:
        try:
            # Handle both timezone-aware and naive datetimes
            start_time_str = state_dict['start_time']
            if isinstance(start_time_str, str):
                start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            else:
                # If it's already a datetime object
                start_time = start_time_str
            
            # Make sure start_time is timezone-aware
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
                
            current_time = datetime.now(timezone.utc)
            elapsed_seconds = (current_time - start_time).total_seconds()
            
            logger.debug(f"ETA calculation: elapsed_seconds={elapsed_seconds}")
            
            if elapsed_seconds > 0:
                # Use a rolling window approach for more accurate recent speed
                # If we've been running for less than 5 minutes, use overall average
                if elapsed_seconds < 300:
                    files_per_second = current_progress / elapsed_seconds
                else:
                    # For longer scans, give more weight to recent progress
                    # Assume last 10% of progress is representative of current speed
                    recent_window = 0.1  # 10% window
                    recent_progress = max(1, int(current_progress * recent_window))
                    recent_time = elapsed_seconds * recent_window
                    
                    # Calculate weighted average (70% recent, 30% overall)
                    recent_rate = recent_progress / recent_time if recent_time > 0 else 0
                    overall_rate = current_progress / elapsed_seconds
                    files_per_second = (recent_rate * 0.7) + (overall_rate * 0.3)
                
                logger.debug(f"ETA calculation: files_per_second={files_per_second}, "
                            f"remaining={total_progress - current_progress}")
                
                # Calculate ETA if we have a valid total and are not complete
                # During discovery/adding phases, use an estimate based on current rate
                if files_per_second > 0:
                    if total_progress > 0 and total_progress > current_progress:
                        # Normal case: we know the total
                        remaining_files = total_progress - current_progress
                        eta_seconds = remaining_files / files_per_second
                        
                        # Apply smoothing for more stable ETA
                        # Use a minimum threshold to avoid jumping ETAs
                        if eta_seconds < 30:  # Less than 30 seconds
                            # Show "Less than 1 minute" instead of 0s
                            eta_seconds = 60
                        elif eta_seconds < 120:  # Less than 2 minutes
                            # Round to nearest 30 seconds
                            eta_seconds = round(eta_seconds / 30) * 30
                        elif eta_seconds < 600:  # Less than 10 minutes
                            # Round to nearest minute
                            eta_seconds = round(eta_seconds / 60) * 60
                        else:
                            # For longer ETAs, round to nearest 5 minutes
                            eta_seconds = round(eta_seconds / 300) * 300
                        
                        eta_time = current_time.timestamp() + eta_seconds
                        eta = datetime.fromtimestamp(eta_time, tz=timezone.utc).isoformat()
                        logger.debug(f"ETA calculated (known total): {eta}, smoothed from {remaining_files / files_per_second:.1f}s")
                    elif current_phase in ['discovering', 'adding'] and current_progress > 10:
                        # During discovery/adding, estimate based on typical scan sizes
                        # Use a heuristic: if we're discovering, assume we'll find similar number of files
                        estimated_total = current_progress * 2  # Conservative estimate
                        remaining_files = estimated_total - current_progress
                        eta_seconds = remaining_files / files_per_second
                        # Cap ETA to reasonable values (max 24 hours)
                        eta_seconds = min(eta_seconds, 86400)
                        # Apply same smoothing
                        if eta_seconds < 60:
                            eta_seconds = 60
                        elif eta_seconds < 120:
                            eta_seconds = round(eta_seconds / 30) * 30
                        elif eta_seconds < 600:
                            eta_seconds = round(eta_seconds / 60) * 60
                        else:
                            eta_seconds = round(eta_seconds / 300) * 300
                        eta_time = current_time.timestamp() + eta_seconds
                        eta = datetime.fromtimestamp(eta_time, tz=timezone.utc).isoformat()
                        logger.debug(f"ETA calculated (estimated): {eta}")
        except Exception as e:
            logger.warning(f"Could not calculate ETA: {e}")
    
    # Convert timestamps to configured timezone
    start_time_tz = None
    end_time_tz = None
    
    if state_dict.get('start_time'):
        try:
            start_time_str = state_dict['start_time']
            if isinstance(start_time_str, str):
                start_dt = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            else:
                start_dt = start_time_str
            
            # Make timezone-aware if needed
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            
            # Convert to configured timezone
            start_time_tz = from_utc_to_configured(start_dt).isoformat() if from_utc_to_configured(start_dt) else None
        except Exception as e:
            logger.warning(f"Could not convert start_time to timezone: {e}")
            start_time_tz = state_dict.get('start_time')
    
    if state_dict.get('end_time'):
        try:
            end_time_str = state_dict['end_time']
            if isinstance(end_time_str, str):
                end_dt = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
            else:
                end_dt = end_time_str
            
            # Make timezone-aware if needed
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            
            # Convert to configured timezone
            end_time_tz = from_utc_to_configured(end_dt).isoformat() if from_utc_to_configured(end_dt) else None
        except Exception as e:
            logger.warning(f"Could not convert end_time to timezone: {e}")
            end_time_tz = state_dict.get('end_time')
    
    # Get current file being processed - ensure full path is shown
    current_file_path = state_dict.get('current_file', service_status.get('file', ''))
    # Ensure we show the full file path, not just directory
    # Check if path looks like a directory (ends with common directory-only names)
    if current_file_path and current_file_path.endswith(('/encoded-video', '/thumbs', '/library', '/uploads')):
        # If it appears to be a directory, indicate that we're scanning within it
        current_file_path = f"{current_file_path} (scanning directory)"
    
    # Build comprehensive status response with frontend-expected fields
    status = {
        'current': current_progress,
        'total': total_progress,
        'file': current_file_path,
        'status': status_value,
        'is_running': is_running,
        'is_scanning': is_running,  # Legacy compatibility
        'is_active': state_dict.get('is_active', False),  # Database active state
        'scan_id': state_dict.get('id'),
        'start_time': start_time_tz,
        'end_time': end_time_tz,
        'directories': state_dict.get('directories'),
        'force_rescan': state_dict.get('force_rescan'),
        'phase': current_phase,
        
        # Frontend-expected progress fields for proper 33% per phase calculation
        'phase_number': phase_number,
        'total_phases': total_phases,
        'phase_current': phase_current,
        'phase_total': phase_total,
        'progress_message': progress_message,
        
        # ETA fields - ensure we don't send None
        'eta': eta if eta else None,  # Let jsonify handle None properly
        'files_per_second': round(files_per_second, 2) if files_per_second > 0 else 0
    }
    
    # Debug log the complete response
    logger.info(f"API scan-status response: progress_message='{status['progress_message']}', "
                f"file='{status['file']}', eta='{status['eta']}', "
                f"current={status['current']}, total={status['total']}")
    
    return jsonify(status)

@scan_bp.route('/cancel-scan', methods=['POST'])
@rate_limit("10 per minute")
@auth_required
def cancel_scan():
    """Cancel the current scan"""
    logger.info("Cancel scan endpoint called")
    try:
        result = current_app.scan_service.cancel_scan()
        logger.info(f"Cancel scan successful: {result}")
        return jsonify(result)
    except RuntimeError as e:
        logger.error(f"Cancel scan failed: {str(e)}")
        return jsonify({'error': str(e)}), 400

@scan_bp.route('/force-cleanup-scan', methods=['POST'])
@scan_bp.route('/scan/recovery', methods=['POST'])  # New consolidated endpoint
@rate_limit("5 per minute")
@auth_required
def force_cleanup_scan():
    """Force cleanup of stuck scan states - emergency recovery endpoint
    
    Note: This consolidates multiple recovery endpoints:
    - /reset-stuck-scans (deprecated)
    - /recover-stuck-scan (deprecated)
    - /force-cleanup-scan (kept for compatibility)
    """
    logger.warning("Force cleanup scan endpoint called - emergency recovery")
    try:
        # Find all active scans
        active_scans = ScanState.query.filter_by(is_active=True).all()
        cleaned_count = 0
        
        for scan in active_scans:
            logger.warning(f"Force cleaning up scan {scan.scan_id} in phase {scan.phase}")
            scan.is_active = False
            scan.phase = 'crashed'
            scan.error_message = 'Force cleaned up by admin'
            scan.end_time = datetime.now(timezone.utc)
            cleaned_count += 1
        
        # Also stop any thread-based scans
        if current_app.scan_service.is_scan_running():
            current_app.scan_service.cancel_scan()
            logger.warning("Cancelled thread-based scan")
        
        db.session.commit()
        
        message = f"Force cleaned up {cleaned_count} stuck scan(s)"
        logger.warning(message)
        return jsonify({
            'status': 'success',
            'message': message,
            'cleaned_count': cleaned_count
        })
    except Exception as e:
        logger.error(f"Force cleanup failed: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@scan_bp.route('/scan-parallel', methods=['POST'])
@rate_limit("2 per minute")
@auth_required
def scan_parallel():
    """Start a parallel scan with multiple workers"""
    # Check if a scan is already running (thread or Celery)
    if is_scan_running():
        # Get current scan status for more informative error message
        scan_state = ScanState.get_or_create()
        if scan_state and scan_state.is_active:
            phase_info = f" (Phase: {scan_state.phase}, Files processed: {scan_state.files_processed})"
        else:
            phase_info = ""
        return jsonify({
            'error': f'A scan is already in progress{phase_info}. Please wait for it to complete or use /api/cancel-scan to stop it.'
        }), 409
    
    data = request.get_json() or {}
    force_rescan = data.get('force_rescan', False)
    num_workers = data.get('num_workers', 4)
    scan_dirs = data.get('directories', [])
    file_paths = data.get('file_paths', [])
    
    # Check if we're scanning specific files
    if file_paths:
        # Scan specific files only
        logger.info(f"Scanning {len(file_paths)} specific files")
        try:
            # P1 Implementation: Use Celery task queue for file scanning
            celery_enabled = check_celery_available()
            
            if celery_enabled:
                # Use Celery task queue
                from pixelprobe.tasks import scan_files_task
                from uuid import uuid4

                # Generate scan ID
                scan_id = str(uuid4())

                # Queue the file scan task with parallel workers
                task = scan_files_task.delay(
                    scan_id=scan_id,
                    file_paths=file_paths,
                    force_rescan=force_rescan,
                    num_workers=num_workers  # Pass num_workers for parallel scanning
                )

                logger.info(f"Queued file scan task {task.id} for {len(file_paths)} files with {num_workers} workers")
                
                return jsonify({
                    'status': 'queued',
                    'scan_id': scan_id,
                    'task_id': task.id,
                    'file_count': len(file_paths),
                    'message': 'File scan queued successfully using Celery task queue',
                    'celery_enabled': True
                })
            else:
                # Fallback to direct scan service
                result = current_app.scan_service.scan_files(
                    file_paths, 
                    force_rescan=force_rescan,
                    num_workers=num_workers
                )
                result['celery_enabled'] = False
                return jsonify(result)
                
        except RuntimeError as e:
            return jsonify({'error': str(e)}), 409
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
    
    # Otherwise scan directories
    # If no directories provided, use configured ones
    if not scan_dirs:
        from models import ScanConfiguration
        configs = ScanConfiguration.query.filter_by(is_active=True).all()
        scan_dirs = [config.path for config in configs]
        
        # If no database config, fall back to environment variable
        if not scan_dirs:
            scan_paths_env = os.environ.get('SCAN_PATHS', '')
            scan_dirs = [path.strip() for path in scan_paths_env.split(',') if path.strip()]
            logger.info(f"Using SCAN_PATHS from environment: {scan_dirs}")
    
    if not scan_dirs:
        return jsonify({'error': 'No directories configured for scanning. Set SCAN_PATHS environment variable or configure paths in the admin interface.'}), 400
    
    # Validate directories
    validated_dirs = []
    for dir_path in scan_dirs:
        try:
            validated_path = validate_directory_path(dir_path)
            validated_dirs.append(validated_path)
        except Exception as e:
            AuditLogger.log_security_event('invalid_scan_directory', str(e), 'warning')
            return jsonify({'error': f'Invalid directory path: {dir_path}'}), 400
    
    AuditLogger.log_action('scan_parallel', {'directories': validated_dirs, 'force_rescan': force_rescan, 'num_workers': num_workers})
    
    # P1 Implementation: Use Celery task queue for parallel directory scanning
    try:
        # Check if Celery is available
        celery_enabled = check_celery_available()
        
        if celery_enabled:
            # Use Celery task queue for parallel scanning
            from pixelprobe.tasks import scan_media_task
            from uuid import uuid4
            
            # Generate scan ID
            scan_id = str(uuid4())
            
            # Determine scan type based on options
            scan_type = 'parallel'
            
            # Queue the scan task
            task = scan_media_task.delay(
                scan_id=scan_id,
                paths=validated_dirs,
                scan_type=scan_type,
                force_rescan=force_rescan
            )
            
            logger.info(f"Queued parallel scan task {task.id} for scan_id {scan_id}")
            
            return jsonify({
                'status': 'queued',
                'scan_id': scan_id,
                'task_id': task.id,
                'scan_type': scan_type,
                'directories': validated_dirs,
                'message': 'Parallel scan queued successfully using Celery task queue',
                'celery_enabled': True
            })
        else:
            # Fallback to direct scan service with parallel workers
            result = current_app.scan_service.scan_directories(
                validated_dirs, 
                force_rescan=force_rescan, 
                num_workers=num_workers
            )
            result['celery_enabled'] = False
            return jsonify(result)
            
    except RuntimeError as e:
        error_msg = str(e)
        # Provide more context for common errors
        if 'already running' in error_msg.lower():
            error_msg = f'{error_msg}. Use /api/scan-status to check progress or /api/cancel-scan to stop the current scan.'
        return jsonify({'error': error_msg, 'suggestion': 'Check /api/scan-status for current scan progress'}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

@scan_bp.route('/reset-stuck-scans', methods=['POST'])
@rate_limit("5 per minute")
@auth_required
def reset_stuck_scans():
    """[DEPRECATED - Use /scan/recovery instead] Reset files that are stuck in 'scanning' state and clear active scan state"""
    try:
        # CRITICAL: Also reset any active scan state to allow new scans
        active_scans = ScanState.query.filter_by(is_active=True).all()
        scan_state_count = 0
        
        for scan in active_scans:
            logger.warning(f"Resetting stuck scan state {scan.scan_id} in phase {scan.phase}")
            scan.is_active = False
            scan.phase = 'crashed'
            scan.error_message = 'Reset due to stuck scan'
            scan.end_time = datetime.now(timezone.utc)
            scan_state_count += 1
        
        # Find all results stuck in 'scanning' state
        stuck_results = ScanResult.query.filter_by(scan_status='scanning').all()
        count = len(stuck_results)
        
        # Reset them to 'pending'
        for result in stuck_results:
            result.scan_status = 'pending'
            result.error_message = 'Reset from stuck scanning state'
        
        db.session.commit()
        
        # Also ensure the scan service knows no scan is running
        if hasattr(current_app, 'scan_service'):
            current_app.scan_service.current_scan_id = None
        
        return jsonify({
            'message': f'Reset {count} stuck files and {scan_state_count} active scans',
            'count': count,
            'scan_states_reset': scan_state_count
        })
    except Exception as e:
        logger.error(f"Error resetting stuck scans: {e}")
        return jsonify({'error': str(e)}), 500

@scan_bp.route('/reset-for-rescan', methods=['POST'])
@rate_limit("5 per minute")
@auth_required
def reset_for_rescan():
    """Reset files for rescanning based on criteria"""
    data = request.get_json() or {}
    reset_type = data.get('type', 'all')
    file_ids = data.get('file_ids', [])
    
    try:
        if reset_type == 'selected' and file_ids:
            # Reset specific files
            results = ScanResult.query.filter(ScanResult.id.in_(file_ids)).all()
            count = len(results)
            for result in results:
                result.scan_status = 'pending'
                result.is_corrupted = False
                result.marked_as_good = False
                result.error_message = None
                result.scan_output = None
        
        elif reset_type == 'corrupted':
            # Reset all corrupted files
            results = ScanResult.query.filter_by(is_corrupted=True, marked_as_good=False).all()
            count = len(results)
            for result in results:
                result.scan_status = 'pending'
                result.is_corrupted = False
                result.error_message = None
                result.scan_output = None
        
        elif reset_type == 'error':
            # Reset all files with errors
            results = ScanResult.query.filter_by(scan_status='error').all()
            count = len(results)
            for result in results:
                result.scan_status = 'pending'
                result.is_corrupted = False
                result.error_message = None
                result.scan_output = None
        
        else:  # all
            # Reset all files
            results = ScanResult.query.all()
            count = len(results)
            for result in results:
                result.scan_status = 'pending'
                result.is_corrupted = False
                result.marked_as_good = False
                result.error_message = None
                result.scan_output = None
        
        db.session.commit()
        
        return jsonify({
            'message': f'Reset {count} files for rescanning',
            'count': count,
            'type': reset_type
        })
        
    except Exception as e:
        logger.error(f"Error resetting files for rescan: {e}")
        return jsonify({'error': str(e)}), 500

@scan_bp.route('/recover-stuck-scan', methods=['POST'])
@rate_limit("5 per minute")
@auth_required
def recover_stuck_scan():
    """[DEPRECATED - Use /scan/recovery instead] Attempt to recover from a stuck scan state"""
    try:
        # Check if scan is actually stuck (has progress but not running)
        is_running = current_app.scan_service.is_scan_running()
        scan_state = ScanState.get_or_create()
        
        # A scan is stuck if:
        # 1. Database says it's active but service says it's not running
        # 2. It's in scanning phase with files processed > 0
        is_stuck = (scan_state.is_active and not is_running and 
                   scan_state.phase in ['discovering', 'adding', 'scanning'] and
                   scan_state.files_processed > 0)
        
        if not is_stuck:
            return jsonify({
                'message': 'No stuck scan detected',
                'is_active': scan_state.is_active,
                'is_running': is_running,
                'phase': scan_state.phase
            })
        
        logger.warning(f"Recovering stuck scan: phase={scan_state.phase}, files_processed={scan_state.files_processed}")
        
        # Mark the scan as errored
        scan_state.error_scan('Scan was stuck and has been recovered')
        db.session.commit()
        
        # Reset any files stuck in 'scanning' status
        stuck_results = ScanResult.query.filter_by(scan_status='scanning').all()
        stuck_count = len(stuck_results)
        for result in stuck_results:
            result.scan_status = 'pending'
            result.error_message = 'Reset from stuck scanning state'
        
        if stuck_count > 0:
            db.session.commit()
            logger.info(f"Reset {stuck_count} files from 'scanning' to 'pending' status")
        
        # Clear the service's internal state
        current_app.scan_service.scan_cancelled = False
        current_app.scan_service.current_scan_thread = None
        
        return jsonify({
            'message': 'Scan state recovered successfully',
            'stuck_files_reset': stuck_count,
            'previous_phase': scan_state.phase,
            'files_processed': scan_state.files_processed
        })
        
    except Exception as e:
        logger.error(f"Error recovering stuck scan: {e}")
        return jsonify({'error': str(e)}), 500

@scan_bp.route('/force-scan-pending', methods=['POST'])
@rate_limit("2 per minute")
@auth_required
def force_scan_pending():
    """Force scan all pending files regardless of directory"""
    try:
        # Count pending files
        pending_count = ScanResult.query.filter_by(scan_status='pending').count()
        
        if pending_count == 0:
            return jsonify({
                'message': 'No pending files to scan',
                'pending_count': 0
            })
        
        # Check if a scan is already running (thread or Celery)
        if is_scan_running():
            return jsonify({'error': 'A scan is already in progress'}), 409
        
        # P1 Implementation: Use Celery task queue for pending files scan
        celery_enabled = check_celery_available()
        
        if celery_enabled:
            # Use Celery task queue for pending scan
            from pixelprobe.tasks import scan_media_task
            from uuid import uuid4
            
            # Generate scan ID
            scan_id = str(uuid4())
            
            # Queue the pending files scan task using special marker
            task = scan_media_task.delay(
                scan_id=scan_id,
                paths=['PENDING_FILES_SCAN'],  # Special marker for pending files
                scan_type='pending',
                force_rescan=False
            )
            
            logger.info(f"Queued pending files scan task {task.id} for {pending_count} files")
            
            return jsonify({
                'status': 'queued',
                'scan_id': scan_id,
                'task_id': task.id,
                'message': f'Pending files scan queued for {pending_count} files using Celery task queue',
                'pending_count': pending_count,
                'celery_enabled': True
            })
        else:
            # Fallback to direct scan service
            from config import Config
            result = current_app.scan_service.scan_directories(
                directories=['PENDING_FILES_SCAN'],  # Special marker
                force_rescan=False,
                num_workers=Config.MAX_WORKERS  # Use configured MAX_WORKERS instead of hardcoded 1
            )
            
            result['celery_enabled'] = False
            result['pending_count'] = pending_count
            return jsonify({
                'message': f'Started scan for {pending_count} pending files',
                'pending_count': pending_count,
                'status': result.get('status', 'started')
            })
        
    except Exception as e:
        logger.error(f"Error starting pending files scan: {e}")
        return jsonify({'error': str(e)}), 500

@scan_bp.route('/reset-files-by-path', methods=['POST'])
@rate_limit("5 per minute")
@auth_required
def reset_files_by_path():
    """Reset specific files by their paths"""
    data = request.get_json() or {}
    file_path = data.get('file_path')
    file_paths = data.get('file_paths', [])
    
    if file_path:
        file_paths = [file_path]
    
    if not file_paths:
        return jsonify({'error': 'No file paths provided'}), 400
    
    try:
        # Reset files by path
        results = ScanResult.query.filter(ScanResult.file_path.in_(file_paths)).all()
        count = len(results)
        for result in results:
            result.scan_status = 'pending'
            result.is_corrupted = False
            result.marked_as_good = False
            result.error_message = None
            result.scan_output = None
        
        db.session.commit()
        
        return jsonify({
            'message': f'Reset {count} files for rescanning',
            'reset_count': count
        })
        
    except Exception as e:
        logger.error(f"Error resetting files by path: {e}")
        return jsonify({'error': str(e)}), 500

@scan_bp.route('/reset-incomplete-scans', methods=['POST'])
@rate_limit("2 per minute")
@auth_required
def reset_incomplete_scans():
    """Reset files that were marked as completed but have incomplete scan data
    
    These are files that show 'N/A' for Tool Details and Scan Date in the UI
    due to the v2.2.59 chunk query bug that prevented actual scanning.
    """
    try:
        # Find files marked as completed but missing scan details
        # OR files marked as healthy/not corrupted but never actually scanned
        incomplete_files = ScanResult.query.filter(
            db.or_(
                # Case 1: Marked as completed but no scan data
                db.and_(
                    ScanResult.scan_status == 'completed',
                    db.or_(
                        ScanResult.scan_date.is_(None),
                        ScanResult.scan_output.is_(None),
                        ScanResult.scan_output == '',
                        ScanResult.scan_output == 'N/A'
                    )
                ),
                # Case 2: Marked as healthy (is_corrupted=False) but no scan date
                db.and_(
                    ScanResult.is_corrupted == False,
                    ScanResult.scan_date.is_(None)
                ),
                # Case 3: Any file with scan_date NULL regardless of status
                # This catches all files that were never actually scanned
                ScanResult.scan_date.is_(None)
            )
        ).all()
        
        count = len(incomplete_files)
        
        if count == 0:
            return jsonify({
                'message': 'No incomplete scans found',
                'reset_count': 0
            })
        
        # Reset these files to pending
        for result in incomplete_files:
            result.scan_status = 'pending'
            result.is_corrupted = None  # Reset to unknown
            result.marked_as_good = False
            result.error_message = 'Reset due to incomplete scan data'
            result.scan_output = None
            # Keep discovered_date as is
        
        db.session.commit()
        
        logger.info(f"Reset {count} files with incomplete scan data to pending status")
        
        return jsonify({
            'message': f'Reset {count} files with incomplete scan data for rescanning',
            'reset_count': count,
            'description': 'These files were marked as completed but had no actual scan results'
        })
        
    except Exception as e:
        logger.error(f"Error resetting incomplete scans: {e}")
        return jsonify({'error': str(e)}), 500

@scan_bp.route('/diagnose-incomplete-scans', methods=['GET'])
@rate_limit("5 per minute")
@auth_required
def diagnose_incomplete_scans():
    """Diagnose why files show as healthy but have N/A scan details
    
    Returns detailed information about files that appear incomplete
    """
    try:
        # Sample some files to understand the data patterns
        diagnostics = {
            'total_files': db.session.query(ScanResult).count(),
            'files_with_null_scan_date': db.session.query(ScanResult).filter(
                ScanResult.scan_date.is_(None)
            ).count(),
            'files_marked_healthy_no_scan_date': db.session.query(ScanResult).filter(
                ScanResult.is_corrupted == False,
                ScanResult.scan_date.is_(None)
            ).count(),
            'files_marked_corrupted_no_scan_date': db.session.query(ScanResult).filter(
                ScanResult.is_corrupted == True,
                ScanResult.scan_date.is_(None)
            ).count(),
            'files_completed_no_scan_date': db.session.query(ScanResult).filter(
                ScanResult.scan_status == 'completed',
                ScanResult.scan_date.is_(None)
            ).count(),
            'files_pending': db.session.query(ScanResult).filter(
                ScanResult.scan_status == 'pending'
            ).count()
        }
        
        # Get a sample of problematic files
        sample_files = []
        problematic = ScanResult.query.filter(
            ScanResult.is_corrupted == False,
            ScanResult.scan_date.is_(None)
        ).limit(5).all()
        
        for file in problematic:
            sample_files.append({
                'file_path': file.file_path,
                'scan_status': file.scan_status,
                'is_corrupted': file.is_corrupted,
                'scan_date': str(file.scan_date) if file.scan_date else None,
                'scan_output': file.scan_output[:100] if file.scan_output else None,
                'discovered_date': str(file.discovered_date) if file.discovered_date else None
            })
        
        diagnostics['sample_problematic_files'] = sample_files
        
        return jsonify(diagnostics)
        
    except Exception as e:
        logger.error(f"Error diagnosing incomplete scans: {e}")
        return jsonify({'error': str(e)}), 500

@scan_bp.route('/diagnose-pending-files', methods=['GET'])
@rate_limit("5 per minute")
@auth_required
def diagnose_pending_files():
    """Investigate files stuck in pending status

    Returns detailed information about pending files including:
    - When they were discovered
    - If they were discovered during recent scans
    - Whether they match scan criteria
    """
    try:
        # Get all pending files with details
        pending_files = ScanResult.query.filter(
            ScanResult.scan_status == 'pending'
        ).order_by(ScanResult.discovered_date.desc()).all()

        # Get recent scans for comparison
        recent_scans = ScanState.query.order_by(
            ScanState.start_time.desc()
        ).limit(5).all()

        diagnostics = {
            'total_pending': len(pending_files),
            'recent_scans': []
        }

        for scan in recent_scans:
            scan_info = {
                'scan_id': scan.scan_id,
                'start_time': str(scan.start_time) if scan.start_time else None,
                'end_time': str(scan.end_time) if scan.end_time else None,
                'phase': scan.phase,
                'files_processed': scan.files_processed,
                'estimated_total': scan.estimated_total
            }
            diagnostics['recent_scans'].append(scan_info)

        # Categorize pending files
        pending_details = []
        for file in pending_files[:30]:  # Limit to 30 for readability
            file_info = {
                'file_path': file.file_path,
                'file_type': file.file_type,
                'discovered_date': str(file.discovered_date) if file.discovered_date else None,
                'file_size': file.file_size,
                'scan_status': file.scan_status
            }

            # Check if discovered during recent scans
            if file.discovered_date and recent_scans:
                for scan in recent_scans:
                    if scan.start_time and scan.end_time:
                        if scan.start_time <= file.discovered_date <= scan.end_time:
                            file_info['discovered_during_scan'] = scan.scan_id
                            break

            pending_details.append(file_info)

        diagnostics['pending_files'] = pending_details

        return jsonify(diagnostics)

    except Exception as e:
        logger.error(f"Error diagnosing pending files: {e}")
        return jsonify({'error': str(e)}), 500

@scan_bp.route('/worker-status')
@exempt_from_rate_limit
@auth_required
def get_worker_status():
    """Get Celery worker status and information"""
    try:
        if not check_celery_available():
            return jsonify({
                'status': 'disabled',
                'message': 'Celery is not configured',
                'workers': []
            })
        
        # Get worker stats from Celery
        stats = current_app.celery.control.inspect().stats()
        active_tasks = current_app.celery.control.inspect().active()
        
        if not stats:
            return jsonify({
                'status': 'offline',
                'message': 'No workers are currently connected',
                'workers': []
            })
        
        workers = []
        for worker_name, worker_stats in stats.items():
            worker_info = {
                'name': worker_name,
                'status': 'online',
                'pool': worker_stats.get('pool', {}).get('implementation', 'unknown'),
                'max_concurrency': worker_stats.get('pool', {}).get('max-concurrency', 0),
                'current_tasks': len(active_tasks.get(worker_name, [])) if active_tasks else 0,
                'total_tasks': worker_stats.get('total', {})
            }
            workers.append(worker_info)
        
        return jsonify({
            'status': 'online',
            'message': f'{len(workers)} worker(s) connected',
            'workers': workers
        })
    except Exception as e:
        logger.error(f"Error getting worker status: {e}")
        return jsonify({
            'status': 'error',
            'message': f'Could not retrieve worker status: {str(e)}',
            'workers': []
        })

@scan_bp.route('/scan-output/<int:result_id>')
@auth_required
def get_scan_output(result_id):
    """Get the detailed scan output for a specific result"""
    result = ScanResult.query.get_or_404(result_id)
    
    return jsonify({
        'id': result.id,
        'file_path': result.file_path,
        'scan_output': result.scan_output,
        'error_message': result.error_message,
        'is_corrupted': result.is_corrupted,
        'scan_status': result.scan_status
    })
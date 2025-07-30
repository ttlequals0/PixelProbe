from flask import Blueprint, request, jsonify, current_app
import pytz
import os
import threading
import logging
from datetime import datetime, timezone

from media_checker import PixelProbe, load_exclusions
from models import db, ScanResult, ScanState
from version import __version__

from pixelprobe.utils.security import (
    validate_file_path, validate_directory_path, 
    PathTraversalError, AuditLogger, validate_json_input
)
# Remove direct limiter imports as we'll use decorators

logger = logging.getLogger(__name__)

# Get timezone from environment variable, default to UTC
APP_TIMEZONE = os.environ.get('TZ', 'UTC')
try:
    tz = pytz.timezone(APP_TIMEZONE)
except pytz.exceptions.UnknownTimeZoneError:
    tz = pytz.UTC
    logger.warning(f"Unknown timezone '{APP_TIMEZONE}', falling back to UTC")

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
    """Check if a scan is currently running"""
    return current_app.scan_service.is_scan_running()

@scan_bp.route('/scan-results')
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
            (ScanResult.marked_as_good == False)
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
    
    # Paginate
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    # Build response
    results = []
    for result in pagination.items:
        result_dict = result.to_dict()
        
        # Convert timestamps to configured timezone
        if result.scan_date:
            if result.scan_date.tzinfo is None:
                scan_date = tz.localize(result.scan_date)
            else:
                scan_date = result.scan_date
            result_dict['scan_date'] = scan_date.astimezone(tz).isoformat()
        
        if result.discovered_date:
            if result.discovered_date.tzinfo is None:
                discovered_date = tz.localize(result.discovered_date)
            else:
                discovered_date = result.discovered_date
            result_dict['discovered_date'] = discovered_date.astimezone(tz).isoformat()
        
        if result.creation_date:
            if result.creation_date.tzinfo is None:
                creation_date = tz.localize(result.creation_date)
            else:
                creation_date = result.creation_date  
            result_dict['creation_date'] = creation_date.astimezone(tz).isoformat()
        
        if result.last_modified:
            if result.last_modified.tzinfo is None:
                last_modified = tz.localize(result.last_modified)
            else:
                last_modified = result.last_modified
            result_dict['last_modified'] = last_modified.astimezone(tz).isoformat()
        
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
def get_scan_result(result_id):
    """Get a single scan result by ID"""
    result = ScanResult.query.get_or_404(result_id)
    result_dict = result.to_dict()
    
    # Convert timestamps to configured timezone
    if result.scan_date:
        if result.scan_date.tzinfo is None:
            scan_date = tz.localize(result.scan_date)
        else:
            scan_date = result.scan_date
        result_dict['scan_date'] = scan_date.astimezone(tz).isoformat()
    
    if result.discovered_date:
        if result.discovered_date.tzinfo is None:
            discovered_date = tz.localize(result.discovered_date)
        else:
            discovered_date = result.discovered_date
        result_dict['discovered_date'] = discovered_date.astimezone(tz).isoformat()
    
    if result.creation_date:
        if result.creation_date.tzinfo is None:
            creation_date = tz.localize(result.creation_date)
        else:
            creation_date = result.creation_date  
        result_dict['creation_date'] = creation_date.astimezone(tz).isoformat()
    
    if result.last_modified:
        if result.last_modified.tzinfo is None:
            last_modified = tz.localize(result.last_modified)
        else:
            last_modified = result.last_modified
        result_dict['last_modified'] = last_modified.astimezone(tz).isoformat()
    
    return jsonify(result_dict)

@scan_bp.route('/scan-file', methods=['POST'])
@rate_limit("5 per minute")
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
    
    # Use scan service
    try:
        result = current_app.scan_service.scan_single_file(validated_path, force_rescan=True)
        return jsonify(result)
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 409
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404

@scan_bp.route('/scan-all', methods=['POST', 'OPTIONS'])
@rate_limit("2 per minute")
@validate_json_input({
    'force_rescan': {'required': False, 'type': bool},
    'directories': {'required': False, 'type': list}
})
def scan_all():
    """Start scanning all media files in configured directories"""
    if request.method == 'OPTIONS':
        return '', 200
    
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
            scan_paths_env = os.environ.get('SCAN_PATHS', '/media')
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
    
    # Use scan service
    try:
        result = current_app.scan_service.scan_directories(
            validated_dirs, 
            force_rescan=force_rescan, 
            num_workers=1
        )
        return jsonify(result)
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

@scan_bp.route('/scan-status')
@exempt_from_rate_limit
def get_scan_status():
    """Get current scan status and progress"""
    # Get progress from scan service
    service_status = current_app.scan_service.get_scan_progress()
    
    # Force fresh database read - bypass session cache for threading
    # This ensures we see updates made by worker threads
    try:
        # Clear any cached objects to ensure fresh read
        db.session.expunge_all()
        # First try to get active scan
        scan_state = db.session.query(ScanState).filter_by(is_active=True).first()
        if scan_state:
            # Force refresh from database to get latest worker thread updates
            db.session.refresh(scan_state)
        else:
            # No active scan, get the most recent one for status display
            scan_state = db.session.query(ScanState).order_by(ScanState.id.desc()).first()
            if not scan_state:
                # No scan states at all, create initial one
                scan_state = ScanState()
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
        progress_message = state_dict.get('progress_message', "Discovering files...")
        # For discovery, we don't know total files yet, so show indeterminate progress
        phase_current = 0
        phase_total = 0  # Will show base phase progress (0-33%)
        
    elif current_phase == 'adding':
        phase_number = 2  
        # Use the actual progress message from database if available
        progress_message = state_dict.get('progress_message', "Adding new files to database...")
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
        
    # Calculate ETA if scan is running and we have progress
    eta = None
    files_per_second = 0
    logger.debug(f"ETA calculation: is_running={is_running}, start_time={state_dict.get('start_time')}, "
                 f"current={current_progress}, total={total_progress}")
    
    if is_running and state_dict.get('start_time') and current_progress > 0:
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
                files_per_second = current_progress / elapsed_seconds
                
                logger.debug(f"ETA calculation: files_per_second={files_per_second}, "
                            f"remaining={total_progress - current_progress}")
                
                if files_per_second > 0 and total_progress > current_progress:
                    remaining_files = total_progress - current_progress
                    eta_seconds = remaining_files / files_per_second
                    eta_time = current_time.timestamp() + eta_seconds
                    eta = datetime.fromtimestamp(eta_time, tz=tz).isoformat()
                    logger.debug(f"ETA calculated: {eta}")
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
            start_time_tz = start_dt.astimezone(tz).isoformat()
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
            end_time_tz = end_dt.astimezone(tz).isoformat()
        except Exception as e:
            logger.warning(f"Could not convert end_time to timezone: {e}")
            end_time_tz = state_dict.get('end_time')
    
    # Build comprehensive status response with frontend-expected fields
    status = {
        'current': current_progress,
        'total': total_progress,
        'file': state_dict.get('current_file', service_status.get('file', '')),
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
        
        # ETA fields
        'eta': eta,
        'files_per_second': round(files_per_second, 2) if files_per_second > 0 else 0
    }
    
    # Debug log the complete response
    logger.info(f"API scan-status response: progress_message='{status['progress_message']}', "
                f"file='{status['file']}', eta='{status['eta']}', "
                f"current={status['current']}, total={status['total']}")
    
    return jsonify(status)

@scan_bp.route('/cancel-scan', methods=['POST'])
@rate_limit("10 per minute")
def cancel_scan():
    """Cancel the current scan"""
    try:
        result = current_app.scan_service.cancel_scan()
        return jsonify(result)
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 400

@scan_bp.route('/scan-parallel', methods=['POST'])
@rate_limit("2 per minute")
def scan_parallel():
    """Start a parallel scan with multiple workers"""
    data = request.get_json() or {}
    force_rescan = data.get('force_rescan', False)
    num_workers = data.get('num_workers', 4)
    scan_dirs = data.get('directories', [])
    file_paths = data.get('file_paths', [])
    deep_scan = data.get('deep_scan', False)
    
    # Check if we're scanning specific files
    if file_paths:
        # Scan specific files only
        logger.info(f"Scanning {len(file_paths)} specific files")
        try:
            result = current_app.scan_service.scan_files(
                file_paths, 
                force_rescan=force_rescan,
                deep_scan=deep_scan,
                num_workers=num_workers
            )
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
            scan_paths_env = os.environ.get('SCAN_PATHS', '/media')
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
    
    # Use scan service with parallel workers
    try:
        result = current_app.scan_service.scan_directories(
            validated_dirs, 
            force_rescan=force_rescan, 
            num_workers=num_workers,
            deep_scan=deep_scan
        )
        return jsonify(result)
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

@scan_bp.route('/reset-stuck-scans', methods=['POST'])
@rate_limit("5 per minute")
def reset_stuck_scans():
    """Reset files that are stuck in 'scanning' state"""
    try:
        # Find all results stuck in 'scanning' state
        stuck_results = ScanResult.query.filter_by(scan_status='scanning').all()
        count = len(stuck_results)
        
        # Reset them to 'pending'
        for result in stuck_results:
            result.scan_status = 'pending'
            result.error_message = 'Reset from stuck scanning state'
        
        db.session.commit()
        
        return jsonify({
            'message': f'Reset {count} stuck files',
            'count': count
        })
    except Exception as e:
        logger.error(f"Error resetting stuck scans: {e}")
        return jsonify({'error': str(e)}), 500

@scan_bp.route('/reset-for-rescan', methods=['POST'])
@rate_limit("5 per minute")
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
def recover_stuck_scan():
    """Attempt to recover from a stuck scan state"""
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

@scan_bp.route('/reset-files-by-path', methods=['POST'])
@rate_limit("5 per minute")
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

@scan_bp.route('/scan-output/<int:result_id>')
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
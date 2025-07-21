from flask import Blueprint, request, jsonify, current_app
import pytz
import os
import threading
import logging

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

# Global state tracking - will be moved to service layer
current_scan_thread = None
scan_cancelled = False
scan_progress = {'current': 0, 'total': 0, 'file': '', 'status': 'idle'}
progress_lock = threading.Lock()

def is_scan_running():
    """Check if a scan is currently running"""
    return current_scan_thread is not None and current_scan_thread.is_alive()

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
        query = query.filter_by(has_warnings=True)
    
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
        # Ensure all datetime fields are timezone-aware
        scan_date = result.scan_date
        if scan_date and scan_date.tzinfo is None:
            scan_date = tz.localize(scan_date)
        
        discovered_date = result.discovered_date
        if discovered_date and discovered_date.tzinfo is None:
            discovered_date = tz.localize(discovered_date)
        
        last_modified = result.last_modified
        if last_modified and last_modified.tzinfo is None:
            last_modified = tz.localize(last_modified)
            
        results.append({
            'id': result.id,
            'file_path': result.file_path,
            'file_name': os.path.basename(result.file_path) if result.file_path else '',
            'file_size': result.file_size,
            'scan_date': scan_date.isoformat() if scan_date else None,
            'discovered_date': discovered_date.isoformat() if discovered_date else None,
            'last_modified': last_modified.isoformat() if last_modified else None,
            'file_hash': result.file_hash,
            'scan_status': result.scan_status,
            'error_message': result.error_message,
            'is_corrupted': result.is_corrupted,
            'marked_as_good': result.marked_as_good,
            'media_info': result.media_info,
            'file_exists': result.file_exists,
            'corruption_details': result.corruption_details,
            'scan_output': result.scan_output,
            'has_warnings': result.has_warnings,
            'warning_details': result.warning_details,
            'file_type': result.file_type,
            'scan_tool': result.scan_tool
        })
    
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
    return jsonify(result.to_dict())

@scan_bp.route('/scan-file', methods=['POST'])
@rate_limit("5 per minute")
@validate_json_input({
    'file_path': {'required': True, 'type': str, 'max_length': 1000}
})
def scan_file():
    """Scan a single file for corruption"""
    global current_scan_thread, scan_cancelled, scan_progress
    
    data = request.get_json()
    file_path = data['file_path']
    
    # Validate file path for security
    try:
        validated_path = validate_file_path(file_path)
        AuditLogger.log_action('scan_file', {'file_path': validated_path})
    except PathTraversalError as e:
        AuditLogger.log_security_event('path_traversal_attempt', str(e), 'warning')
        return jsonify({'error': 'Invalid file path'}), 400
    
    # Check if scan is already running
    if is_scan_running():
        return jsonify({'error': 'Another scan is already in progress'}), 409
    
    # Initialize progress
    with progress_lock:
        scan_progress = {'current': 0, 'total': 1, 'file': file_path, 'status': 'scanning'}
    scan_cancelled = False
    
    # Create scan thread - capture app instance for thread context
    app = current_app._get_current_object()
    def run_single_scan():
        # Need app context for database operations in thread
        with app.app_context():
            try:
                excluded_paths, excluded_extensions = load_exclusions()
                checker = PixelProbe(
                    database_path=app.config['SQLALCHEMY_DATABASE_URI'],
                    excluded_paths=excluded_paths,
                    excluded_extensions=excluded_extensions
                )
                checker.scan_file(validated_path, force_rescan=True)
                
                with progress_lock:
                    scan_progress['current'] = 1
                    scan_progress['status'] = 'completed'
                    
            except Exception as e:
                logger.error(f"Error scanning file: {e}")
                with progress_lock:
                    scan_progress['status'] = 'error'
                    scan_progress['error'] = str(e)
    
    current_scan_thread = threading.Thread(target=run_single_scan)
    current_scan_thread.start()
    
    return jsonify({
        'message': 'Scan started',
        'file_path': validated_path
    })

@scan_bp.route('/scan-all', methods=['POST', 'OPTIONS'])
@rate_limit("2 per minute")
@validate_json_input({
    'force_rescan': {'required': False, 'type': bool},
    'directories': {'required': False, 'type': list}
})
def scan_all():
    """Start scanning all media files in configured directories"""
    global current_scan_thread, scan_cancelled, scan_progress
    
    if request.method == 'OPTIONS':
        return '', 200
    
    # Check if scan is already running
    if is_scan_running():
        return jsonify({'error': 'Another scan is already in progress'}), 409
    
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
    
    # Initialize progress
    with progress_lock:
        scan_progress = {'current': 0, 'total': 0, 'file': '', 'status': 'initializing'}
    scan_cancelled = False
    
    # Save scan state
    scan_state = ScanState.get_or_create()
    scan_state.start_scan(validated_dirs, force_rescan)
    db.session.commit()
    
    # Create scan thread - capture app instance for thread context
    app = current_app._get_current_object()
    def run_full_scan():
        # Need app context for database operations in thread
        with app.app_context():
            try:
                excluded_paths, excluded_extensions = load_exclusions()
                checker = PixelProbe(
                database_path=app.config['SQLALCHEMY_DATABASE_URI'],
                excluded_paths=excluded_paths,
                excluded_extensions=excluded_extensions
                )
                
                # Discovery phase
                with progress_lock:
                    scan_progress['status'] = 'discovering'
                
                all_files = []
                logger.info(f"Starting discovery with directories: {validated_dirs} (type: {type(validated_dirs)})")
                if not scan_cancelled:
                    # Filter only existing directories
                    existing_dirs = [d for d in validated_dirs if os.path.exists(d)]
                    if existing_dirs:
                        all_files = checker.discover_media_files(existing_dirs)
                    else:
                        logger.warning("No valid directories found for scanning")
                
                if scan_cancelled:
                    with progress_lock:
                        scan_progress['status'] = 'cancelled'
                    scan_state.cancel_scan()
                    db.session.commit()
                    return
                
                # Update progress
                with progress_lock:
                    scan_progress['total'] = len(all_files)
                    scan_progress['current'] = 0
                    scan_progress['status'] = 'scanning'
                
                # Scanning phase
                for i, file_path in enumerate(all_files):
                    if scan_cancelled:
                        break
                        
                    with progress_lock:
                        scan_progress['current'] = i
                        scan_progress['file'] = file_path
                    
                    try:
                        checker.scan_file(file_path, force_rescan=force_rescan)
                    except Exception as e:
                        logger.error(f"Error scanning file {file_path}: {e}")
                    
                    # Update scan state progress
                    scan_state.update_progress(i + 1, len(all_files))
                    db.session.commit()
                
                # Complete scan
                if scan_cancelled:
                    with progress_lock:
                        scan_progress['status'] = 'cancelled'
                    scan_state.cancel_scan()
                else:
                    with progress_lock:
                        scan_progress['status'] = 'completed'
                        scan_progress['current'] = scan_progress['total']
                    scan_state.complete_scan()
                
                db.session.commit()
                
            except Exception as e:
                logger.error(f"Error during scan: {e}")
                with progress_lock:
                    scan_progress['status'] = 'error'
                    scan_progress['error'] = str(e)
                scan_state.error_scan(str(e))
                db.session.commit()
    
    current_scan_thread = threading.Thread(target=run_full_scan)
    current_scan_thread.start()
    
    return jsonify({
        'message': 'Scan started',
        'directories': validated_dirs,
        'force_rescan': force_rescan
    })

@scan_bp.route('/scan-status')
def get_scan_status():
    """Get current scan status and progress"""
    with progress_lock:
        status = scan_progress.copy()
    
    # Add scan state info
    scan_state = ScanState.get_or_create()
    state_dict = scan_state.to_dict()
    
    # Merge progress info with state info
    status.update({
        'is_running': is_scan_running(),
        'scan_id': state_dict.get('id'),
        'start_time': state_dict.get('start_time'),
        'end_time': state_dict.get('end_time'),
        'directories': state_dict.get('directories'),
        'force_rescan': state_dict.get('force_rescan')
    })
    
    return jsonify(status)

@scan_bp.route('/cancel-scan', methods=['POST'])
@rate_limit("10 per minute")
def cancel_scan():
    """Cancel the current scan"""
    global scan_cancelled
    
    if not is_scan_running():
        return jsonify({'error': 'No scan is currently running'}), 400
    
    scan_cancelled = True
    
    # Update scan state
    scan_state = ScanState.get_or_create()
    scan_state.cancel_scan()
    db.session.commit()
    
    return jsonify({'message': 'Scan cancellation requested'})

@scan_bp.route('/scan-parallel', methods=['POST'])
@rate_limit("2 per minute")
def scan_parallel():
    """Start a parallel scan with multiple workers"""
    global current_scan_thread, scan_cancelled, scan_progress
    
    # Check if scan is already running
    if is_scan_running():
        return jsonify({'error': 'Another scan is already in progress'}), 409
    
    data = request.get_json() or {}
    force_rescan = data.get('force_rescan', False)
    num_workers = data.get('num_workers', 4)
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
    
    # Initialize progress
    with progress_lock:
        scan_progress = {'current': 0, 'total': 0, 'file': '', 'status': 'initializing'}
    scan_cancelled = False
    
    # Save scan state
    scan_state = ScanState.get_or_create()
    scan_state.start_scan(validated_dirs, force_rescan)
    db.session.commit()
    
    # Create scan thread - capture app instance for thread context
    app = current_app._get_current_object()
    def run_parallel_scan():
        # Need app context for database operations in thread
        with app.app_context():
            try:
                excluded_paths, excluded_extensions = load_exclusions()
                checker = PixelProbe(
                database_path=app.config['SQLALCHEMY_DATABASE_URI'],
                excluded_paths=excluded_paths,
                excluded_extensions=excluded_extensions
                )
                
                # Discovery phase
                with progress_lock:
                    scan_progress['status'] = 'discovering'
                
                all_files = []
                if not scan_cancelled:
                    # Filter only existing directories
                    existing_dirs = [d for d in validated_dirs if os.path.exists(d)]
                    if existing_dirs:
                        all_files = checker.discover_media_files(existing_dirs)
                    else:
                        logger.warning("No valid directories found for scanning")
                
                if scan_cancelled:
                    with progress_lock:
                        scan_progress['status'] = 'cancelled'
                    scan_state.cancel_scan()
                    db.session.commit()
                    return
                
                # Update progress
                with progress_lock:
                    scan_progress['total'] = len(all_files)
                    scan_progress['current'] = 0
                    scan_progress['status'] = 'scanning'
                
                # Parallel scanning
                from concurrent.futures import ThreadPoolExecutor, as_completed
                
                def scan_single_file(file_path):
                    if scan_cancelled:
                        return None
                    try:
                        return checker.scan_file(file_path, force_rescan=force_rescan)
                    except Exception as e:
                        logger.error(f"Error scanning file {file_path}: {e}")
                        return None
                
                completed = 0
                with ThreadPoolExecutor(max_workers=num_workers) as executor:
                    # Submit all files for scanning
                    future_to_file = {executor.submit(scan_single_file, f): f for f in all_files}
                    
                    # Process completed scans
                    for future in as_completed(future_to_file):
                        if scan_cancelled:
                            executor.shutdown(wait=False)
                            break
                        
                        file_path = future_to_file[future]
                        completed += 1
                        
                        with progress_lock:
                            scan_progress['current'] = completed
                            scan_progress['file'] = file_path
                        
                        # Update scan state progress
                        scan_state.update_progress(completed, len(all_files))
                        db.session.commit()
                
                # Complete scan
                if scan_cancelled:
                    with progress_lock:
                        scan_progress['status'] = 'cancelled'
                    scan_state.cancel_scan()
                else:
                    with progress_lock:
                        scan_progress['status'] = 'completed'
                        scan_progress['current'] = scan_progress['total']
                    scan_state.complete_scan()
                
                db.session.commit()
                
            except Exception as e:
                logger.error(f"Error during parallel scan: {e}")
                with progress_lock:
                    scan_progress['status'] = 'error'
                    scan_progress['error'] = str(e)
                scan_state.error_scan(str(e))
                db.session.commit()
    
    current_scan_thread = threading.Thread(target=run_parallel_scan)
    current_scan_thread.start()
    
    return jsonify({
        'message': 'Parallel scan started',
        'directories': scan_dirs,
        'force_rescan': force_rescan,
        'num_workers': num_workers
    })

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
    global current_scan_thread, scan_cancelled, scan_progress
    
    try:
        # Force reset scan state
        current_scan_thread = None
        scan_cancelled = False
        
        with progress_lock:
            scan_progress = {'current': 0, 'total': 0, 'file': '', 'status': 'idle'}
        
        # Reset database scan state
        scan_state = ScanState.get_or_create()
        if scan_state.phase == 'scanning':
            scan_state.error_scan('Scan was stuck and has been recovered')
            db.session.commit()
        
        # Reset any files stuck in scanning state
        stuck_results = ScanResult.query.filter_by(scan_status='scanning').all()
        for result in stuck_results:
            result.scan_status = 'pending'
            result.error_message = 'Reset from stuck scanning state'
        
        db.session.commit()
        
        return jsonify({
            'message': 'Scan state recovered successfully',
            'stuck_files_reset': len(stuck_results)
        })
        
    except Exception as e:
        logger.error(f"Error recovering stuck scan: {e}")
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
import os
import logging
from datetime import datetime, timezone
import pytz
from flask import Flask, request, jsonify, send_file, render_template, Response
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from dotenv import load_dotenv
import threading
import time
import signal
from contextlib import contextmanager
from pathlib import Path
import csv
import io

from media_checker import PixelProbe
from models import db, ScanResult, IgnoredErrorPattern, ScanSchedule, ScanConfiguration, ScanState, CleanupState, FileChangesState
from version import __version__, __github_url__
from scheduler import MediaScheduler

load_dotenv()

# Timeout context manager for system info
class TimeoutException(Exception):
    pass

@contextmanager
def timeout(duration):
    def timeout_handler(signum, frame):
        raise TimeoutException(f"Operation timed out after {duration} seconds")
    
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(duration)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

# Get timezone from environment variable, default to UTC
APP_TIMEZONE = os.environ.get('TZ', 'UTC')
try:
    tz = pytz.timezone(APP_TIMEZONE)
    logger = logging.getLogger(__name__)
    logger.info(f"Using timezone: {APP_TIMEZONE}")
except pytz.exceptions.UnknownTimeZoneError:
    tz = pytz.UTC
    logger = logging.getLogger(__name__)
    logger.warning(f"Unknown timezone '{APP_TIMEZONE}', falling back to UTC")

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-in-production')
# Use environment variable or default to local database
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Configure SQLAlchemy for better SQLite handling
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,  # Test connections before using them
    'pool_recycle': 300,  # Recycle connections after 5 minutes
    'connect_args': {
        'timeout': 30,  # Increased timeout to 30 seconds for long operations
        'check_same_thread': False,
        # SQLite-specific pragmas for better concurrency
        'isolation_level': 'DEFERRED',  # Better concurrency than default
    }
}

db.init_app(app)
CORS(app, resources={
    r"/api/*": {"origins": "*"},
    r"/": {"origins": "*"}
})

# Initialize scheduler (will be initialized after app context is ready)
scheduler = MediaScheduler()

def create_performance_indexes():
    """Create performance indexes on application startup"""
    try:
        from sqlalchemy import text
        
        # List of indexes to create
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_scan_status ON scan_results(scan_status)",
            "CREATE INDEX IF NOT EXISTS idx_scan_date ON scan_results(scan_date)",
            "CREATE INDEX IF NOT EXISTS idx_is_corrupted ON scan_results(is_corrupted)",
            "CREATE INDEX IF NOT EXISTS idx_marked_as_good ON scan_results(marked_as_good)",
            "CREATE INDEX IF NOT EXISTS idx_discovered_date ON scan_results(discovered_date)",
            "CREATE INDEX IF NOT EXISTS idx_file_hash ON scan_results(file_hash)",
            "CREATE INDEX IF NOT EXISTS idx_last_modified ON scan_results(last_modified)",
            "CREATE INDEX IF NOT EXISTS idx_file_path ON scan_results(file_path)",
            "CREATE INDEX IF NOT EXISTS idx_status_date ON scan_results(scan_status, scan_date)",
            "CREATE INDEX IF NOT EXISTS idx_corrupted_good ON scan_results(is_corrupted, marked_as_good)"
        ]
        
        logger.info("Creating performance indexes...")
        for index_sql in indexes:
            db.session.execute(text(index_sql))
        
        db.session.commit()
        logger.info("Performance indexes created successfully!")
        
    except Exception as e:
        logger.warning(f"Error creating performance indexes (may already exist): {str(e)}")
        db.session.rollback()

# Create indexes on startup
with app.app_context():
    create_performance_indexes()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('media_checker.log')
    ]
)
logger = logging.getLogger(__name__)

# Configure SQLite for better concurrency after logger is available
with app.app_context():
    try:
        from sqlalchemy import text
        db.session.execute(text("PRAGMA journal_mode=WAL"))
        db.session.execute(text("PRAGMA busy_timeout=30000"))  # 30 second timeout
        db.session.execute(text("PRAGMA synchronous=NORMAL"))  # Faster writes, still safe
        db.session.commit()
        logger.info("SQLite WAL mode and optimizations enabled")
    except Exception as e:
        logger.warning(f"Could not enable SQLite optimizations: {str(e)}")

@app.before_request
def log_request_info():
    logger.info(f"Request: {request.method} {request.url} - Remote: {request.remote_addr}")
    if request.is_json and request.json:
        logger.info(f"Request body: {request.json}")

@app.after_request
def log_response_info(response):
    logger.info(f"Response: {response.status_code} - {request.method} {request.url}")
    return response

# Configuration loaded at module level (no heavy operations)
scan_paths = os.environ.get('SCAN_PATHS', '/media').split(',')
max_files_to_scan = int(os.environ.get('MAX_FILES_TO_SCAN', 0)) or None
max_workers = int(os.environ.get('MAX_SCAN_WORKERS', 4))
system_info_timeout = int(os.environ.get('SYSTEM_INFO_TIMEOUT', '30'))  # 30 seconds default
# batch_commit_size removed - write queue handles all database writes immediately
reset_batch_size = int(os.environ.get('RESET_BATCH_SIZE', '500'))  # Reset batch size

# Lazy initialization - only done when needed
media_checker = None
paths_validated = False

def initialize_media_checker():
    """Initialize and validate paths only when needed (lazy initialization)"""
    global media_checker, paths_validated
    
    if media_checker is None:
        logger.info(f"=== PIXELPROBE STARTUP ===")
        logger.info(f"Scan paths configured: {scan_paths}")
        logger.info(f"Max files to scan: {max_files_to_scan}")
        
        media_checker = PixelProbe(
            max_workers=max_workers,
            excluded_paths=scheduler.excluded_paths,
            excluded_extensions=scheduler.excluded_extensions
        )
        logger.info(f"PixelProbe initialized with {max_workers} max workers")
        
        # Clean up any files stuck in scanning status
        cleanup_stuck_scans()
    
    if not paths_validated:
        logger.info("Validating scan paths...")
        
        for path in scan_paths:
            if os.path.exists(path):
                file_count = sum(len(files) for _, _, files in os.walk(path))
                logger.info(f"Scan path {path} exists with {file_count} total files")
                if file_count == 0:
                    logger.warning(f"Scan path {path} contains no files - verify the path contains media files")
            else:
                logger.error(f"Scan path {path} does not exist - check your SCAN_PATHS configuration")
        
        paths_validated = True
        logger.info("=== STARTUP COMPLETE ===")
    
    return media_checker

# Global scan state tracking with thread safety
scan_state_lock = threading.Lock()
scan_state = {
    'is_scanning': False,
    'start_time': None,
    'files_processed': 0,
    'estimated_total': 0,
    'current_file': None,
    'current_file_start_time': None,
    'average_scan_time': 0,
    'scan_times': [],
    'phase': 'idle',  # idle, discovering, adding, scanning, validating
    'scan_thread_active': False,  # Tracks if scan thread is running
    'progress_message': '',  # Custom progress message
    # Phase-specific progress tracking
    'phase_current': 0,  # Current progress within the phase
    'phase_total': 0,    # Total items in current phase
    'phase_number': 0,   # Which phase number (1, 2, or 3)
    'total_phases': 3,   # Total number of phases
    'discovery_count': 0,  # Files discovered in discovery phase
    'adding_progress': 0,   # Files added to database in adding phase
    'scan_id': None,  # Unique identifier for current scan
    'cancel_requested': False  # Flag to request cancellation
}

# Cleanup orphans operation state - NOW USING DATABASE (CleanupState model)
# Keeping for backwards compatibility but no longer used
cleanup_state_lock = threading.Lock()
cleanup_state = {}

# File changes check operation state
file_changes_state_lock = threading.Lock()
file_changes_state = {
    'is_running': False,
    'start_time': None,
    'files_processed': 0,
    'total_files': 0,
    'changes_found': 0,
    'current_file': None,
    'phase': 'idle',  # idle, checking, complete
    'progress_message': '',
    'cancel_requested': False,
    'batch_start_time': None,
    'batch_number': 0,
    'total_batches': 0
}

# Stats update thread state
stats_update_thread = None
stats_update_stop_event = threading.Event()

# Database write queue for serializing writes
import queue
db_write_queue = queue.Queue()
db_write_thread = None
db_write_stop_event = threading.Event()

def update_scan_state(**kwargs):
    """Thread-safe update of scan state and queue database update"""
    with scan_state_lock:
        scan_id = scan_state.get('scan_id') or kwargs.get('scan_id')
        
        # Update memory state
        for key, value in kwargs.items():
            if key in scan_state:
                scan_state[key] = value
                logger.debug(f"Updated scan state: {key} = {value}")
            else:
                logger.warning(f"Attempted to update unknown scan state key: {key}")
        
        # Queue database update if we have a scan_id
        if scan_id:
            # Filter out non-database fields
            db_updates = {}
            db_fields = ['phase', 'phase_number', 'phase_current', 'phase_total', 
                        'files_processed', 'estimated_total', 'discovery_count',
                        'current_file', 'progress_message', 'error_message']
            
            for key, value in kwargs.items():
                if key in db_fields:
                    db_updates[key] = value
            
            if db_updates:
                db_write_queue.put({
                    'type': 'update_scan_state',
                    'scan_id': scan_id,
                    'updates': db_updates
                })

def get_scan_state_copy():
    """Get a thread-safe copy of the scan state"""
    with scan_state_lock:
        return scan_state.copy()

def start_stats_update_thread():
    """Start a background thread that periodically updates stats from DB"""
    global stats_update_thread
    
    def update_stats_worker():
        """Worker thread that periodically fetches stats from DB"""
        logger.info("Stats update thread started")
        while not stats_update_stop_event.is_set():
            try:
                with app.app_context():
                    # Check if any scan is active in DB
                    active_scan = ScanState.query.filter_by(is_active=True).first()
                    if active_scan:
                        # Update global scan state from DB
                        with scan_state_lock:
                            scan_state['is_scanning'] = True
                            scan_state['scan_thread_active'] = True
                            scan_state['phase'] = active_scan.phase
                            scan_state['phase_number'] = active_scan.phase_number
                            scan_state['phase_current'] = active_scan.phase_current
                            scan_state['phase_total'] = active_scan.phase_total
                            scan_state['files_processed'] = active_scan.files_processed
                            scan_state['estimated_total'] = active_scan.estimated_total
                            scan_state['discovery_count'] = active_scan.discovery_count
                            scan_state['progress_message'] = active_scan.progress_message or ''
                            scan_state['current_file'] = active_scan.current_file
                            scan_state['start_time'] = active_scan.start_time
                            scan_state['scan_id'] = active_scan.scan_id
                        logger.debug(f"Updated scan state from DB - phase: {active_scan.phase}, processed: {active_scan.files_processed}")
                    else:
                        # No active scan in DB
                        with scan_state_lock:
                            if scan_state['is_scanning']:
                                logger.info("No active scan found in DB, resetting scan state")
                            scan_state['is_scanning'] = False
                            scan_state['scan_thread_active'] = False
                            scan_state['phase'] = 'idle'
                            scan_state['scan_id'] = None
                    
                # Sleep for a short interval
                stats_update_stop_event.wait(1.0)  # Update every second
                
            except Exception as e:
                logger.error(f"Error in stats update thread: {str(e)}")
                stats_update_stop_event.wait(5.0)  # Wait longer on error
        
        logger.info("Stats update thread stopped")
    
    # Start the thread if not already running
    if stats_update_thread is None or not stats_update_thread.is_alive():
        stats_update_stop_event.clear()
        stats_update_thread = threading.Thread(target=update_stats_worker, daemon=True)
        stats_update_thread.start()
        logger.info("Started stats update background thread")

def stop_stats_update_thread():
    """Stop the stats update thread"""
    global stats_update_thread
    
    if stats_update_thread and stats_update_thread.is_alive():
        logger.info("Stopping stats update thread...")
        stats_update_stop_event.set()
        stats_update_thread.join(timeout=5.0)
        stats_update_thread = None
        logger.info("Stats update thread stopped")

def start_db_write_thread():
    """Start the database write queue processor thread"""
    global db_write_thread
    
    def db_write_worker():
        """Worker thread that processes database writes from the queue"""
        logger.info("Database write queue thread started")
        
        while not db_write_stop_event.is_set():
            try:
                # Wait for writes with timeout
                try:
                    write_task = db_write_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                if write_task is None:  # Poison pill to stop thread
                    break
                
                task_type = write_task.get('type')
                
                try:
                    with app.app_context():
                        if task_type == 'update_scan_state':
                            # Update scan state in database
                            scan_id = write_task.get('scan_id')
                            updates = write_task.get('updates', {})
                            
                            scan_record = ScanState.query.filter_by(scan_id=scan_id).first()
                            if scan_record:
                                for key, value in updates.items():
                                    if hasattr(scan_record, key):
                                        setattr(scan_record, key, value)
                                db.session.commit()
                                logger.debug(f"Updated scan state in DB: {updates.keys()}")
                        
                        elif task_type == 'update_scan_result':
                            # Update scan result
                            file_path = write_task.get('file_path')
                            updates = write_task.get('updates', {})
                            
                            scan_result = ScanResult.query.filter_by(file_path=file_path).first()
                            if scan_result:
                                for key, value in updates.items():
                                    if hasattr(scan_result, key):
                                        setattr(scan_result, key, value)
                                db.session.commit()
                                logger.debug(f"Updated scan result for {file_path}")
                        
                        elif task_type == 'batch_update_scan_results':
                            # Batch update scan results
                            results = write_task.get('results', [])
                            
                            for result in results:
                                file_path = result.get('file_path')
                                scan_result = ScanResult.query.filter_by(file_path=file_path).first()
                                
                                if scan_result:
                                    # Update existing record
                                    for key, value in result.items():
                                        if hasattr(scan_result, key) and key != 'file_path':
                                            setattr(scan_result, key, value)
                                else:
                                    # Create new record
                                    scan_result = ScanResult(**result)
                                    db.session.add(scan_result)
                            
                            db.session.commit()
                            logger.info(f"Batch updated {len(results)} scan results")
                        
                        elif task_type == 'create_scan_state':
                            # Create new scan state
                            scan_state_data = write_task.get('data', {})
                            scan_state_record = ScanState(**scan_state_data)
                            db.session.add(scan_state_record)
                            db.session.commit()
                            logger.info(f"Created scan state record: {scan_state_record.scan_id}")
                        
                        elif task_type == 'complete_scan' or task_type == 'update_scan_state_complete':
                            # Mark scan as complete
                            scan_id = write_task.get('scan_id')
                            scan_record = ScanState.query.filter_by(scan_id=scan_id).first()
                            if scan_record:
                                scan_record.is_active = False
                                scan_record.phase = 'completed'
                                scan_record.end_time = datetime.now(timezone.utc)
                                db.session.commit()
                                logger.info(f"Marked scan {scan_id} as complete")
                        
                        # Mark task as done
                        db_write_queue.task_done()
                        
                except Exception as e:
                    logger.error(f"Error processing database write task: {str(e)}")
                    db.session.rollback()
                    db_write_queue.task_done()
                    
            except Exception as e:
                logger.error(f"Error in database write thread: {str(e)}")
        
        logger.info("Database write queue thread stopped")
    
    # Start the thread if not already running
    if db_write_thread is None or not db_write_thread.is_alive():
        db_write_stop_event.clear()
        db_write_thread = threading.Thread(target=db_write_worker, daemon=True)
        db_write_thread.start()
        logger.info("Started database write queue thread")

def stop_db_write_thread():
    """Stop the database write thread"""
    global db_write_thread
    
    if db_write_thread and db_write_thread.is_alive():
        logger.info("Stopping database write thread...")
        db_write_stop_event.set()
        db_write_queue.put(None)  # Poison pill
        db_write_thread.join(timeout=5.0)
        db_write_thread = None
        logger.info("Database write thread stopped")

# Check if templates directory exists
templates_dir = os.path.join(os.path.dirname(__file__), 'templates')
if os.path.exists(templates_dir):
    logger.info(f"Templates directory found: {templates_dir}")
    template_files = os.listdir(templates_dir)
    logger.info(f"Template files: {template_files}")
else:
    logger.error(f"Templates directory not found: {templates_dir}")

def cleanup_stuck_scans():
    """Reset any files stuck in 'scanning' status back to 'pending' on startup"""
    with app.app_context():
        try:
            stuck_files = ScanResult.query.filter_by(scan_status='scanning').all()
            if stuck_files:
                logger.info(f"Found {len(stuck_files)} files stuck in 'scanning' status, resetting to 'pending'")
                for file in stuck_files:
                    file.scan_status = 'pending'
                    file.scan_date = None
                    file.corruption_details = None
                db.session.commit()
                logger.info("Successfully reset stuck scanning files")
            else:
                logger.info("No files stuck in 'scanning' status")
        except Exception as e:
            logger.error(f"Error cleaning up stuck scans: {str(e)}")
            db.session.rollback()

# Cleanup and startup completion will be handled in initialize_media_checker()

@app.route('/')
def index():
    logger.info("Main page requested")
    try:
        # Use modern template if USE_MODERN_UI env var is set
        template = 'index_modern.html' if os.environ.get('USE_MODERN_UI', 'true').lower() == 'true' else 'index.html'
        return render_template(template)
    except Exception as e:
        logger.error(f"Error rendering index template: {str(e)}")
        return f"Error loading page: {str(e)}", 500

@app.route('/api-docs')
def api_docs():
    logger.info("API documentation page requested")
    try:
        return render_template('api_docs.html')
    except Exception as e:
        logger.error(f"Error rendering API docs template: {str(e)}")
        return f"Error loading API documentation: {str(e)}", 500

@app.route('/favicon.ico')
def favicon():
    # Serve the PixelProbe favicon
    try:
        return send_file('static/images/favicon.ico', mimetype='image/x-icon')
    except FileNotFoundError:
        # Fallback to a simple 1x1 transparent PNG favicon
        import base64
        favicon_data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')
        response = app.make_response(favicon_data)
        response.headers.set('Content-Type', 'image/png')
        response.headers.set('Cache-Control', 'public, max-age=86400')  # Cache for 1 day
        return response

@app.route('/favicon-16x16.png')
def favicon_16x16():
    # Serve the PixelProbe 16x16 favicon
    try:
        return send_file('static/images/favicon-16x16.png', mimetype='image/png')
    except FileNotFoundError:
        # Fallback to dynamic generation
        import base64
        from io import BytesIO
        try:
            from PIL import Image, ImageDraw, ImageFont
            
            # Create a simple icon
            size = 16
            img = Image.new('RGBA', (size, size), (52, 152, 219, 255))  # Blue background
            draw = ImageDraw.Draw(img)
            
            # Add "PP" text
            try:
                font = ImageFont.load_default()
            except:
                font = None
            
            text = "PP"
            if font:
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
            else:
                text_width = size // 2
                text_height = size // 3
                
            x = (size - text_width) // 2
            y = (size - text_height) // 2
            draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)
            
            # Save to bytes
            output = BytesIO()
            img.save(output, format='PNG')
            icon_data = output.getvalue()
            
        except ImportError:
            # Fallback to a simple colored PNG if PIL is not available
            icon_data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')
        
        response = app.make_response(icon_data)
        response.headers.set('Content-Type', 'image/png')
        response.headers.set('Cache-Control', 'public, max-age=86400')  # Cache for 1 day
        return response

@app.route('/favicon-32x32.png')
def favicon_32x32():
    # Serve the PixelProbe 32x32 favicon
    try:
        return send_file('static/images/favicon-32x32.png', mimetype='image/png')
    except FileNotFoundError:
        # Fallback to dynamic generation
        import base64
        from io import BytesIO
        try:
            from PIL import Image, ImageDraw, ImageFont
            
            # Create a simple icon
            size = 32
            img = Image.new('RGBA', (size, size), (52, 152, 219, 255))  # Blue background
            draw = ImageDraw.Draw(img)
            
            # Add "PP" text
            try:
                font = ImageFont.load_default()
            except:
                font = None
            
            text = "PP"
            if font:
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
            else:
                text_width = size // 2
                text_height = size // 3
                
            x = (size - text_width) // 2
            y = (size - text_height) // 2
            draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)
            
            # Save to bytes
            output = BytesIO()
            img.save(output, format='PNG')
            icon_data = output.getvalue()
            
        except ImportError:
            # Fallback to a simple colored PNG if PIL is not available
            icon_data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')
        
        response = app.make_response(icon_data)
        response.headers.set('Content-Type', 'image/png')
        response.headers.set('Cache-Control', 'public, max-age=86400')  # Cache for 1 day
        return response

@app.route('/static/images/pixelprobe-logo.png')
def logo():
    # Serve the PixelProbe logo image
    try:
        return send_file('static/images/pixelprobe-logo.png', mimetype='image/png')
    except FileNotFoundError:
        return "Logo not found", 404

@app.route('/health')
def health_check():
    logger.info("Health check requested")
    return jsonify({
        'status': 'healthy',
        'scan_paths': scan_paths,
        'max_files': max_files_to_scan,
        'database_ready': True
    })

@app.route('/api/version')
def version_check():
    logger.info("Version check requested")
    return jsonify({
        'version': __version__,
        'timestamp': datetime.now().isoformat(),
        'scan_paths': scan_paths,
        'max_files': max_files_to_scan
    })

@app.route('/api/scan-results')
def get_scan_results():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    search = request.args.get('search', '', type=str)
    sort_field = request.args.get('sort_field', 'scan_date', type=str)
    sort_order = request.args.get('sort_order', 'desc', type=str)
    filter_type = request.args.get('filter', 'all', type=str)
    
    logger.info(f"Fetching scan results - page: {page}, per_page: {per_page}, search: '{search}', sort: {sort_field} {sort_order}, filter: {filter_type}")
    
    # Build query with optional search filter
    query = ScanResult.query
    if search:
        query = query.filter(ScanResult.file_path.contains(search))
    
    # Add corruption filter
    if filter_type == 'corrupted':
        # Show only corrupted files that don't have warnings and aren't marked as good
        query = query.filter(
            (ScanResult.is_corrupted == True) & 
            ((ScanResult.has_warnings == False) | (ScanResult.has_warnings == None)) &
            (ScanResult.marked_as_good == False)
        )
    elif filter_type == 'healthy':
        # Show only healthy files (no corruption, no warnings, or marked as good)
        query = query.filter(
            ((ScanResult.is_corrupted == False) & 
             ((ScanResult.has_warnings == False) | (ScanResult.has_warnings == None))) |
            (ScanResult.marked_as_good == True)
        )
    elif filter_type == 'warning':
        # Show files with warnings that aren't marked as good
        query = query.filter(
            (ScanResult.has_warnings == True) &
            (ScanResult.marked_as_good == False)
        )
    # 'all' filter - no additional filtering needed
    
    # Add sorting
    sort_column = getattr(ScanResult, sort_field, ScanResult.scan_date)
    if sort_order.lower() == 'asc':
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())
    
    # Handle "All" option (per_page = -1)
    if per_page == -1:
        # Get all results without pagination
        all_results = query.all()
        results = {
            'items': all_results,
            'total': len(all_results),
            'has_prev': False,
            'has_next': False,
            'pages': 1
        }
    else:
        results = query.paginate(
            page=page, per_page=per_page, error_out=False
        )
    
    # Handle logging for both paginated and non-paginated results
    if per_page == -1:
        logger.info(f"Retrieved all {len(results['items'])} results")
    else:
        logger.info(f"Retrieved {len(results.items)} results (total: {results.total})")
    
    # Handle response for both paginated and non-paginated results
    if per_page == -1:
        return jsonify({
            'results': [result.to_dict() for result in results['items']],
            'total': results['total'],
            'pages': results['pages'],
            'current_page': 1,
            'sort_field': sort_field,
            'sort_order': sort_order,
            'filter': filter_type
        })
    else:
        return jsonify({
            'results': [result.to_dict() for result in results.items],
            'total': results.total,
            'pages': results.pages,
            'current_page': page,
            'sort_field': sort_field,
            'sort_order': sort_order,
            'filter': filter_type
        })

@app.route('/api/scan-results/<int:result_id>')
def get_scan_result(result_id):
    result = ScanResult.query.get_or_404(result_id)
    return jsonify(result.to_dict())



@app.route('/api/scan-file', methods=['POST'])
def scan_single_file():
    logger.info("=== SINGLE FILE SCAN ENDPOINT HIT ===")
    data = request.get_json()
    file_path = data.get('file_path')
    deep_scan = data.get('deep_scan', False)
    
    logger.info(f"Single file scan requested: {file_path} (deep_scan: {deep_scan})")
    logger.info(f"Request data: {data}")
    logger.info(f"Request method: {request.method}")
    logger.info(f"Request headers: {dict(request.headers)}")
    
    if not file_path or not os.path.exists(file_path):
        logger.warning(f"File not found for scan: {file_path}")
        return jsonify({'error': 'File not found'}), 400
    
    scan_type = "deep scan" if deep_scan else "basic scan"
    logger.info(f"Starting {scan_type} of file: {file_path}")
    checker = initialize_media_checker()
    result = checker.scan_file(file_path, deep_scan)
    
    logger.info(f"Scan completed for {file_path} - Corrupted: {result['is_corrupted']}")
    if result['is_corrupted']:
        logger.warning(f"Corruption detected in {file_path}: {result['corruption_details']}")
    
    # Check if record already exists
    existing_record = ScanResult.query.filter_by(file_path=file_path).first()
    
    if existing_record:
        # Update existing record
        logger.info(f"Updating existing scan record for {file_path}")
        existing_record.file_size = result['file_size']
        existing_record.file_type = result['file_type']
        existing_record.creation_date = result['creation_date']
        existing_record.last_modified = result.get('last_modified')
        existing_record.is_corrupted = result['is_corrupted']
        existing_record.corruption_details = result['corruption_details']
        existing_record.scan_date = datetime.now(timezone.utc)
        existing_record.scan_status = 'completed'
        existing_record.file_hash = result.get('file_hash')
        existing_record.scan_tool = result.get('scan_tool')
        existing_record.scan_duration = result.get('scan_duration')
        existing_record.scan_output = result.get('scan_output')
        existing_record.has_warnings = result.get('has_warnings', False)
        existing_record.warning_details = result.get('warning_details')
        scan_result = existing_record
    else:
        # Create new record
        logger.info(f"Creating new scan record for {file_path}")
        scan_result = ScanResult(
            file_path=result['file_path'],
            file_size=result['file_size'],
            file_type=result['file_type'],
            creation_date=result['creation_date'],
            last_modified=result.get('last_modified'),
            is_corrupted=result['is_corrupted'],
            corruption_details=result['corruption_details'],
            scan_date=datetime.now(timezone.utc),
            scan_status='completed',
            file_hash=result.get('file_hash'),
            scan_tool=result.get('scan_tool'),
            scan_duration=result.get('scan_duration'),
            scan_output=result.get('scan_output'),
            has_warnings=result.get('has_warnings', False),
            warning_details=result.get('warning_details')
        )
        db.session.add(scan_result)
    
    db.session.commit()
    
    logger.info(f"Scan result saved to database for {file_path}")
    
    return jsonify(scan_result.to_dict())

@app.route('/api/scan-all', methods=['POST', 'OPTIONS'])
def scan_all_files():
    logger.info("=== SCAN-ALL ENDPOINT HIT ===")
    logger.info("Full scan request received - starting background scan")
    logger.info(f"Request content type: {request.content_type}")
    logger.info(f"Request data: {request.get_data()}")
    logger.info(f"Request method: {request.method}")
    logger.info(f"Request headers: {dict(request.headers)}")
    
    # Handle OPTIONS request for CORS
    if request.method == 'OPTIONS':
        logger.info("Handling OPTIONS request for CORS")
        return '', 200
    
    # Check if a scan is already in progress (check both memory and DB)
    active_scan = ScanState.query.filter_by(is_active=True).first()
    if active_scan or scan_state['is_scanning']:
        logger.warning("Scan already in progress, rejecting new scan request")
        # Ensure stats update thread is running to sync state
        start_stats_update_thread()
        return jsonify({'error': 'Scan already in progress'}), 409
    
    # Validate JSON content type
    if not request.is_json:
        logger.error(f"Invalid content type: {request.content_type}")
        return jsonify({'error': 'Content-Type must be application/json'}), 400
    
    # Try to get JSON data
    try:
        data = request.get_json(force=True)
        logger.info(f"JSON data received: {data}")
    except Exception as e:
        logger.error(f"Failed to parse JSON: {str(e)}")
        return jsonify({'error': 'Invalid JSON data'}), 400
    
    # Create scan state in database via queue
    import uuid
    scan_id = str(uuid.uuid4())
    
    # Queue the scan state creation
    db_write_queue.put({
        'type': 'create_scan_state',
        'data': {
            'scan_id': scan_id,
            'is_active': True,
            'phase': 'initializing',
            'start_time': datetime.now(timezone.utc),
            'progress_message': 'Initializing scan...'
        }
    })
    logger.info(f"Queued scan state creation with ID: {scan_id}")
    
    # Set initial scan state immediately
    update_scan_state(
        is_scanning=True,
        start_time=datetime.now(),
        files_processed=0,
        estimated_total=0,
        scan_thread_active=True,
        phase='initializing',
        current_file=None,
        progress_message='Initializing scan...',
        scan_id=scan_id,
        cancel_requested=False  # Reset cancel flag
    )
    
    # Start stats update thread to monitor progress
    start_stats_update_thread()
    
    def run_scan():
        with app.app_context():
            try:
                logger.info(f"=== FULL SCAN STARTED ===")
                logger.info(f"Scan paths: {scan_paths}")
                logger.info(f"Max files limit: {max_files_to_scan}")
                
                # Update progress for path validation
                update_scan_state(
                    progress_message='Validating scan paths...',
                    phase='validating',
                    is_scanning=True,
                    scan_thread_active=True
                )
                
                # Initialize media checker and validate paths
                checker = initialize_media_checker()
                
                # Show path validation progress
                for path in scan_paths:
                    if os.path.exists(path):
                        file_count = sum(len(files) for _, _, files in os.walk(path))
                        update_scan_state(progress_message=f'Scan path {path} exists with {file_count} total files')
                        logger.info(f"Scan path {path} exists with {file_count} total files")
                    else:
                        update_scan_state(progress_message=f'Scan path {path} does not exist')
                        logger.error(f"Scan path {path} does not exist")
                
                # Phase 1: Discover files (excluding files already in database)
                update_scan_state(
                    phase='discovering',
                    phase_number=1,
                    phase_current=0,
                    phase_total=0,
                    progress_message='Phase 1 of 3: Discovering new files...'
                )
                logger.info("Phase 1: Discovering files...")
                # Load existing files in batches to avoid memory issues
                existing_files = set()
                batch_size = 10000
                offset = 0
                total_existing = ScanResult.query.count()
                logger.info(f"Loading {total_existing} existing file paths in batches...")
                
                while True:
                    batch = ScanResult.query.order_by(ScanResult.id).offset(offset).limit(batch_size).all()
                    if not batch:
                        break
                    existing_files.update(result.file_path for result in batch)
                    offset += batch_size
                    if offset % 50000 == 0:
                        logger.info(f"Loaded {offset} file paths...")
                
                logger.info(f"Found {len(existing_files)} files already in database")
                update_scan_state(progress_message=f'Phase 1 of 3: Found {len(existing_files)} files already in database')
                
                # Define progress callback for discovery
                def discovery_progress(files_checked, files_discovered):
                    update_scan_state(
                        phase_current=files_checked,
                        phase_total=files_checked,  # We don't know total ahead of time
                        discovery_count=files_discovered,
                        progress_message=f'Phase 1 of 3: Checked {files_checked} files, found {files_discovered} new media files'
                    )
                
                discovered_files = checker.discover_files(scan_paths, max_files_to_scan, existing_files, discovery_progress)
                logger.info(f"Discovered {len(discovered_files)} new files")
                update_scan_state(
                    progress_message=f'Phase 1 of 3: Discovered {len(discovered_files)} new files to scan',
                    discovery_count=len(discovered_files)
                )
                
                # Phase 2: Add discovered files to database as pending
                files_added = 0
                if len(discovered_files) > 0:
                    update_scan_state(
                        phase='adding',
                        phase_number=2,
                        phase_current=0,
                        phase_total=len(discovered_files),
                        progress_message=f'Phase 2 of 3: Adding {len(discovered_files)} new files to database...'
                    )
                    logger.info("Phase 2: Adding discovered files to database...")
                    
                    for file_path in discovered_files:
                        existing = ScanResult.query.filter_by(file_path=file_path).first()
                        if not existing:
                            # Get basic file info
                            file_info = checker.get_file_info(file_path)
                            
                            scan_result = ScanResult(
                                file_path=file_path,
                                file_size=file_info['file_size'],
                                file_type=file_info['file_type'],
                                creation_date=file_info['creation_date'],
                                scan_status='pending'
                            )
                            db.session.add(scan_result)
                            files_added += 1
                            update_scan_state(
                                phase_current=files_added,
                                adding_progress=files_added
                            )
                            
                            # Commit every 100 files to avoid memory issues
                            if files_added % 100 == 0:
                                db.session.commit()
                                logger.info(f"Added {files_added} files to database")
                                update_scan_state(progress_message=f'Phase 2 of 3: Added {files_added}/{len(discovered_files)} files to database...')
                    
                    db.session.commit()
                    logger.info(f"Phase 2 complete: Added {files_added} new files to database")
                    update_scan_state(progress_message=f'Phase 2 of 3: Added {files_added} new files to database')
                else:
                    logger.info("No new files to add to database, proceeding to scan existing pending files")
                    # When skipping phase 2, still update the progress message
                    update_scan_state(
                        phase_number=2,
                        progress_message='Phase 2 of 3: Skipped (no new files to add)'
                    )
                
                # Phase 3: Scan pending files using parallel scanning
                logger.info("Phase 3: Scanning pending files using parallel workers...")
                
                # Get all pending file IDs upfront - much simpler!
                # Memory usage: ~200 bytes per record (ID + path), so 1M files = ~190MB
                logger.info("Loading pending file IDs...")
                pending_records = ScanResult.query.filter_by(scan_status='pending').with_entities(ScanResult.id, ScanResult.file_path).all()
                pending_count = len(pending_records)
                logger.info(f"Found {pending_count} pending files to scan")
                
                # Log memory usage estimate for transparency
                if pending_count > 100000:
                    estimated_mb = (pending_count * 200) / (1024 * 1024)
                    logger.info(f"Estimated memory usage for ID list: {estimated_mb:.1f} MB")
                
                # Ensure scan state is still active before proceeding
                update_scan_state(
                    phase='scanning',
                    phase_number=3,
                    phase_current=0,
                    phase_total=pending_count,
                    estimated_total=pending_count,
                    scan_times=[],
                    is_scanning=True,
                    scan_thread_active=True,
                    progress_message=f'Phase 3 of 3: Starting scan of {pending_count} files...'
                )
                
                if pending_count == 0:
                    logger.info("No pending files found to scan")
                    update_scan_state(progress_message="No pending files to scan")
                    return
                
                scan_start_time = datetime.now()
                
                files_processed = 0
                files_scanned = 0
                corrupted_found = 0
                
                # Process pending files in batches
                batch_size = 1000
                
                # Process files in batches from our ID list
                for batch_start in range(0, pending_count, batch_size):
                    # Check for cancellation
                    with scan_state_lock:
                        if scan_state.get('cancel_requested'):
                            logger.info("Scan cancelled by user")
                            update_scan_state(
                                phase='cancelled',
                                progress_message='Scan cancelled by user',
                                is_scanning=False,
                                scan_thread_active=False
                            )
                            return
                    
                    batch_end = min(batch_start + batch_size, pending_count)
                    batch_records = pending_records[batch_start:batch_end]
                    
                    if not batch_records:
                        break
                    
                    # Get the actual file records for this batch
                    batch_ids = [record.id for record in batch_records]
                    pending_batch = ScanResult.query.filter(ScanResult.id.in_(batch_ids)).all()
                    
                    # Filter out files that don't exist in this batch
                    valid_files_in_batch = []
                    for pending_record in pending_batch:
                        if not os.path.exists(pending_record.file_path):
                            logger.warning(f"File no longer exists: {pending_record.file_path}")
                            pending_record.scan_status = 'error'
                            pending_record.corruption_details = 'File not found'
                            db.session.commit()
                            files_processed += 1
                            continue
                        valid_files_in_batch.append(pending_record.file_path)
                    
                    if not valid_files_in_batch:
                        continue
                    
                    # Process this batch of files using parallel scanning
                    logger.info(f"Processing batch of {len(valid_files_in_batch)} files (offset {offset})")
                    
                    # Use ThreadPoolExecutor for this batch
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    
                    with ThreadPoolExecutor(max_workers=checker.max_workers) as executor:
                        # Submit scan tasks for this batch
                        # First, create a mapping of file paths to their deep_scan settings
                        file_to_deep_scan = {}
                        for record in pending_batch:
                            if os.path.exists(record.file_path):
                                # Use deep_scan flag from database, default to False if not set
                                file_to_deep_scan[record.file_path] = getattr(record, 'deep_scan', False)
                        
                        future_to_file = {
                            executor.submit(checker.scan_file, file_path, file_to_deep_scan.get(file_path, False)): file_path 
                            for file_path in valid_files_in_batch
                        }
                        
                        # Process results as they complete
                        for future in as_completed(future_to_file):
                            # Check for cancellation
                            with scan_state_lock:
                                if scan_state.get('cancel_requested'):
                                    logger.info("Scan cancelled during batch processing")
                                    # Cancel remaining futures
                                    for f in future_to_file:
                                        f.cancel()
                                    update_scan_state(
                                        phase='cancelled',
                                        progress_message='Scan cancelled by user',
                                        is_scanning=False,
                                        scan_thread_active=False
                                    )
                                    return
                            
                            file_path = future_to_file[future]
                            try:
                                result = future.result()
                                files_processed += 1
                                files_scanned += 1
                                
                                if result.get('is_corrupted'):
                                    corrupted_found += 1
                                    logger.warning(f"CORRUPTED FILE: {file_path} - {result.get('corruption_details')}")
                                else:
                                    logger.info(f"HEALTHY FILE: {file_path}")
                                
                                # Update progress
                                percentage = (files_processed / pending_count * 100) if pending_count > 0 else 0
                                file_name = file_path.split('/')[-1] if file_path else ''
                                update_scan_state(
                                    files_processed=files_processed,
                                    phase_current=files_processed,
                                    current_file=file_path,
                                    progress_message=f"Phase 3 of 3: Scanning {file_name} ({files_processed}/{pending_count} - {percentage:.1f}%)"
                                )
                                
                                # Add scan metadata
                                result['scan_date'] = datetime.now(timezone.utc)
                                result['scan_status'] = 'completed'
                                
                                # Send to database write queue
                                db_write_queue.put({
                                    'type': 'batch_update_scan_results',
                                    'results': [result]
                                })
                                
                            except Exception as e:
                                logger.error(f"Error scanning {file_path}: {str(e)}")
                                files_processed += 1
                                # Update database with error
                                error_result = {
                                    'file_path': file_path,
                                    'scan_status': 'error',
                                    'corruption_details': f"Scan error: {str(e)}",
                                    'scan_date': datetime.now(timezone.utc)
                                }
                                db_write_queue.put({
                                    'type': 'batch_update_scan_results',
                                    'results': [error_result]
                                })
                    
                    logger.info(f"Completed batch, processed {files_processed} files total")
                
                # All batches processed - verify we actually processed all pending files
                remaining_pending = ScanResult.query.filter_by(scan_status='pending').count()
                if remaining_pending > 0:
                    logger.warning(f"SCAN INCOMPLETE: {remaining_pending} files still pending after processing {files_processed} files")
                    logger.warning(f"This may indicate a pagination issue. Consider running scan again.")
                
                logger.info(f"=== FULL SCAN COMPLETED ===")
                logger.info(f"Files processed: {files_processed}")
                logger.info(f"Files scanned: {files_scanned}")
                logger.info(f"Corrupted files found: {corrupted_found}")
                logger.info(f"Files still pending: {remaining_pending}")
                
            except Exception as e:
                logger.error(f"CRITICAL ERROR in scan thread: {str(e)}")
                logger.error(f"Exception type: {type(e).__name__}")
                import traceback
                logger.error(f"Full traceback: {traceback.format_exc()}")
                # Don't reset state here - let finally handle it
                
            finally:
                # Mark scan as complete in database via queue
                if scan_state.get('scan_id'):
                    db_write_queue.put({
                        'type': 'update_scan_state_complete',
                        'scan_id': scan_state.get('scan_id')
                    })
                    logger.info(f"Queued scan completion for {scan_state.get('scan_id')}")
                
                # Reset scan state only after all processing is complete
                update_scan_state(
                    is_scanning=False,
                    start_time=None,
                    files_processed=0,
                    estimated_total=0,
                    current_file=None,
                    current_file_start_time=None,
                    average_scan_time=0,
                    scan_times=[],
                    phase='idle',
                    scan_thread_active=False,
                    progress_message='',
                    phase_current=0,
                    phase_total=0,
                    phase_number=0,
                    discovery_count=0,
                    adding_progress=0
                )
    
    try:
        thread = threading.Thread(target=run_scan)
        thread.daemon = True
        thread.start()
        logger.info("Background scan thread started successfully")
        return jsonify({'message': 'Scan started'}), 202
    except Exception as e:
        # Reset scan state on error
        update_scan_state(
            is_scanning=False,
            start_time=None,
            files_processed=0,
            estimated_total=0,
            scan_thread_active=False,
            progress_message='',
            phase='idle',
            phase_current=0,
            phase_total=0,
            phase_number=0
        )
        
        logger.error(f"Failed to start scan thread: {str(e)}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({'error': f'Failed to start scan: {str(e)}'}), 500

@app.errorhandler(400)
def bad_request_handler(error):
    logger.error(f"400 Bad Request: {error}")
    logger.error(f"Request URL: {request.url}")
    logger.error(f"Request method: {request.method}")
    logger.error(f"Request headers: {dict(request.headers)}")
    logger.error(f"Request data: {request.get_data()}")
    return jsonify({'error': 'Bad Request', 'message': str(error)}), 400

@app.errorhandler(500)
def internal_error_handler(error):
    logger.error(f"500 Internal Server Error: {error}")
    return jsonify({'error': 'Internal Server Error', 'message': str(error)}), 500

@app.route('/api/view/<int:result_id>', methods=['GET', 'OPTIONS'])
def view_file(result_id):
    # Handle OPTIONS request for CORS preflight
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Range, Content-Type'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response
    
    result = ScanResult.query.get_or_404(result_id)
    
    logger.info(f"View requested for file: {result.file_path} (ID: {result_id})")
    
    if not os.path.exists(result.file_path):
        logger.error(f"View failed - file not found: {result.file_path}")
        return jsonify({'error': 'File not found'}), 404
    
    # Get file stats
    file_size = os.path.getsize(result.file_path)
    file_type = result.file_type or 'application/octet-stream'
    
    # Handle range requests for video streaming (required for mobile)
    range_header = request.headers.get('range')
    if range_header and file_type.startswith('video/'):
        logger.info(f"Range request for video: {range_header}")
        
        # Parse range header
        try:
            byte_start = int(range_header.split('=')[1].split('-')[0])
            byte_end = file_size - 1
            if '-' in range_header.split('=')[1] and range_header.split('=')[1].split('-')[1]:
                byte_end = int(range_header.split('=')[1].split('-')[1])
            
            # Limit chunk size for mobile
            max_chunk = 1024 * 1024  # 1MB chunks
            if byte_end - byte_start > max_chunk:
                byte_end = byte_start + max_chunk
            
            logger.info(f"Serving bytes {byte_start}-{byte_end}/{file_size}")
            
            def generate():
                with open(result.file_path, 'rb') as f:
                    f.seek(byte_start)
                    remaining = byte_end - byte_start + 1
                    while remaining:
                        chunk_size = min(8192, remaining)
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            
            response = Response(
                generate(),
                206,  # Partial Content
                mimetype=file_type,
                headers={
                    'Content-Range': f'bytes {byte_start}-{byte_end}/{file_size}',
                    'Accept-Ranges': 'bytes',
                    'Content-Length': str(byte_end - byte_start + 1),
                    'Cache-Control': 'no-cache',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'GET, OPTIONS',
                    'Access-Control-Allow-Headers': 'Range'
                }
            )
            return response
            
        except Exception as e:
            logger.error(f"Error handling range request: {e}")
            # Fall through to regular response
    
    # Regular response for non-range requests
    logger.info(f"Serving file for viewing: {result.file_path}")
    response = send_file(result.file_path, as_attachment=False, mimetype=file_type)
    response.headers['Accept-Ranges'] = 'bytes'
    response.headers['Cache-Control'] = 'no-cache'
    # Add CORS headers for mobile compatibility
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Range'
    return response

@app.route('/api/download/<int:result_id>')
def download_file(result_id):
    result = ScanResult.query.get_or_404(result_id)
    
    logger.info(f"Download requested for file: {result.file_path} (ID: {result_id})")
    
    if not os.path.exists(result.file_path):
        logger.error(f"Download failed - file not found: {result.file_path}")
        return jsonify({'error': 'File not found'}), 404
    
    logger.info(f"Starting download of file: {result.file_path}")
    return send_file(result.file_path, as_attachment=True)

@app.route('/api/mark-as-good', methods=['POST'])
def mark_as_good():
    data = request.get_json()
    file_ids = data.get('file_ids', [])
    
    if not file_ids:
        return jsonify({'error': 'No file IDs provided'}), 400
    
    try:
        for file_id in file_ids:
            result = ScanResult.query.get(file_id)
            if result:
                result.marked_as_good = True
                result.is_corrupted = False
                logger.info(f"Marked file as good (healthy): {result.file_path}")
            
        db.session.commit()
        logger.info(f"Successfully marked {len(file_ids)} files as good")
        
        return jsonify({
            'message': f'Successfully marked {len(file_ids)} files as good',
            'marked_files': len(file_ids)
        })
        
    except Exception as e:
        logger.error(f"Error marking files as good: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/reset-stuck-scans', methods=['POST'])
def reset_stuck_scans():
    """Reset files stuck in 'scanning' status back to 'pending'"""
    try:
        # Get count first for reporting
        reset_count = ScanResult.query.filter_by(scan_status='scanning').count()
        
        if reset_count > 0:
            logger.info(f"Resetting {reset_count} files stuck in 'scanning' status")
            
            # Use bulk update for better performance
            ScanResult.query.filter_by(scan_status='scanning').update({
                'scan_status': 'pending',
                'scan_date': None,
                'corruption_details': None,
                'is_corrupted': None
            })
            db.session.commit()
            
            logger.info(f"Successfully reset {reset_count} stuck files")
            
            return jsonify({
                'message': f'Successfully reset {reset_count} files stuck in scanning status',
                'reset_count': reset_count
            })
        else:
            return jsonify({
                'message': 'No files stuck in scanning status',
                'reset_count': 0
            })
            
    except Exception as e:
        logger.error(f"Error resetting stuck scans: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/reset-for-rescan', methods=['POST'])
def reset_for_rescan():
    """Reset files for rescanning based on various criteria"""
    try:
        data = request.get_json() or {}
        reset_type = data.get('reset_type', 'unscanned')
        
        if reset_type == 'all':
            # Reset ALL files to pending for complete rescan
            files_query = ScanResult.query
            logger.info("Resetting ALL files for complete rescan")
        elif reset_type == 'unscanned':
            # Reset only files that were never actually scanned (no scan_date)
            files_query = ScanResult.query.filter(
                (ScanResult.scan_date == None) | 
                (ScanResult.scan_status == 'scanning')
            )
            logger.info("Resetting unscanned files and stuck files")
        elif reset_type == 'errors':
            # Reset files with errors
            files_query = ScanResult.query.filter_by(scan_status='error')
            logger.info("Resetting files with errors")
        else:
            return jsonify({'error': 'Invalid reset_type'}), 400
        
        # Get count first, then process in batches to avoid memory issues
        reset_count = files_query.count()
        logger.info(f"Found {reset_count} files to reset")
        
        if reset_count > 0:
            logger.info(f"Resetting {reset_count} files to pending status")
            
            # Use bulk update for better performance with large datasets
            # Process in batches to avoid memory issues and provide progress feedback
            batch_size = reset_batch_size  # Use the configured batch size directly
            total_batches = (reset_count + batch_size - 1) // batch_size
            
            for batch_num in range(total_batches):
                offset = batch_num * batch_size
                
                # Get IDs for this batch to avoid loading all data into memory
                batch_query = files_query.order_by(ScanResult.id).offset(offset).limit(batch_size)
                batch_ids = [file.id for file in batch_query]
                
                if batch_ids:
                    # Use bulk update for better performance
                    updated_count = db.session.query(ScanResult).filter(ScanResult.id.in_(batch_ids)).update({
                        'scan_status': 'pending',
                        'scan_date': None,
                        'corruption_details': None,
                        'is_corrupted': None,
                        'scan_tool': None,
                        'scan_duration': None,
                        'scan_output': None
                    }, synchronize_session=False)
                    
                    db.session.commit()
                    
                    batch_end = min(offset + batch_size, reset_count)
                    logger.info(f"Reset batch {batch_num + 1} ({batch_end}/{reset_count}) - updated {updated_count} records")
            
            logger.info(f"Successfully reset {reset_count} files for rescanning")
            
            return jsonify({
                'message': f'Successfully reset {reset_count} files for rescanning',
                'reset_count': reset_count,
                'reset_type': reset_type
            })
        else:
            return jsonify({
                'message': f'No files found to reset for type: {reset_type}',
                'reset_count': 0,
                'reset_type': reset_type
            })
            
    except Exception as e:
        logger.error(f"Error resetting files for rescan: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats')
def get_stats():
    try:
        # Use a single query with subqueries for better performance
        stats = db.session.execute(
            db.text("""
                SELECT 
                    COUNT(*) as total_files,
                    SUM(CASE WHEN scan_status = 'completed' THEN 1 ELSE 0 END) as completed_files,
                    SUM(CASE WHEN scan_status = 'pending' THEN 1 ELSE 0 END) as pending_files,
                    SUM(CASE WHEN scan_status = 'scanning' THEN 1 ELSE 0 END) as scanning_files,
                    SUM(CASE WHEN scan_status = 'error' THEN 1 ELSE 0 END) as error_files,
                    SUM(CASE WHEN is_corrupted = 1 AND marked_as_good = 0 AND (has_warnings = 0 OR has_warnings IS NULL) THEN 1 ELSE 0 END) as corrupted_files,
                    SUM(CASE WHEN (is_corrupted = 0 OR marked_as_good = 1) THEN 1 ELSE 0 END) as healthy_files,
                    SUM(CASE WHEN marked_as_good = 1 THEN 1 ELSE 0 END) as marked_as_good,
                    SUM(CASE WHEN has_warnings = 1 AND marked_as_good = 0 THEN 1 ELSE 0 END) as warning_files
                FROM scan_results
            """)
        ).fetchone()
        
        result = {
            'total_files': stats[0] or 0,
            'completed_files': stats[1] or 0,
            'pending_files': stats[2] or 0,
            'scanning_files': stats[3] or 0,
            'error_files': stats[4] or 0,
            'corrupted_files': stats[5] or 0,
            'healthy_files': stats[6] or 0,
            'marked_as_good': stats[7] or 0,
            'warning_files': stats[8] or 0
        }
        
        logger.info(f"Stats requested - Total: {result['total_files']}, Completed: {result['completed_files']}, Pending: {result['pending_files']}, Scanning: {result['scanning_files']}, Corrupted: {result['corrupted_files']}, Healthy: {result['healthy_files']}, Marked Good: {result['marked_as_good']}")
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error getting stats: {str(e)}")
        # Fallback to individual queries if the optimized query fails
        try:
            total_files = ScanResult.query.count()
            completed_files = ScanResult.query.filter_by(scan_status='completed').count()
            pending_files = ScanResult.query.filter_by(scan_status='pending').count()
            scanning_files = ScanResult.query.filter_by(scan_status='scanning').count()
            error_files = ScanResult.query.filter_by(scan_status='error').count()
            
            # Count files, excluding marked_as_good from corrupted and warning counts
            corrupted_files = ScanResult.query.filter(
                (ScanResult.is_corrupted == True) & 
                (ScanResult.marked_as_good == False) &
                ((ScanResult.has_warnings == False) | (ScanResult.has_warnings == None))
            ).count()
            
            warning_files = ScanResult.query.filter(
                (ScanResult.has_warnings == True) &
                (ScanResult.marked_as_good == False)
            ).count()
            
            marked_as_good = ScanResult.query.filter_by(marked_as_good=True).count()
            
            # Files marked as good should be considered healthy
            healthy_files = ScanResult.query.filter(
                (ScanResult.is_corrupted == False) | (ScanResult.marked_as_good == True)
            ).count()
            
            return jsonify({
                'total_files': total_files,
                'completed_files': completed_files,
                'pending_files': pending_files,
                'scanning_files': scanning_files,
                'error_files': error_files,
                'corrupted_files': corrupted_files,
                'healthy_files': healthy_files,
                'marked_as_good': marked_as_good,
                'warning_files': warning_files
            })
        except Exception as e2:
            logger.error(f"Fallback stats query also failed: {str(e2)}")
            return jsonify({'error': 'Database query failed'}), 500

@app.route('/api/system-info')
def get_system_info():
    """Get comprehensive system information - optimized to read from database"""
    try:
        logger.info("System info requested")
        
        # Add overall timeout for the entire endpoint
        start_time = time.time()
        
        # Database statistics - use single query for better performance
        stats_query = db.session.execute(
            text("""
                SELECT 
                    COUNT(*) as total_files,
                    SUM(CASE WHEN scan_status = 'completed' THEN 1 ELSE 0 END) as completed_files,
                    SUM(CASE WHEN scan_status = 'pending' THEN 1 ELSE 0 END) as pending_files,
                    SUM(CASE WHEN scan_status = 'scanning' THEN 1 ELSE 0 END) as scanning_files,
                    SUM(CASE WHEN scan_status = 'error' THEN 1 ELSE 0 END) as error_files,
                    SUM(CASE WHEN is_corrupted = 1 THEN 1 ELSE 0 END) as corrupted_files,
                    SUM(CASE WHEN is_corrupted = 0 THEN 1 ELSE 0 END) as healthy_files,
                    SUM(CASE WHEN marked_as_good = 1 THEN 1 ELSE 0 END) as marked_as_good,
                    SUM(CASE WHEN has_warnings = 1 THEN 1 ELSE 0 END) as warning_files
                FROM scan_results
            """)
        ).fetchone()
        
        db_total_files = stats_query[0] or 0
        db_completed_files = stats_query[1] or 0
        db_pending_files = stats_query[2] or 0
        db_scanning_files = stats_query[3] or 0
        db_error_files = stats_query[4] or 0
        db_corrupted_files = stats_query[5] or 0
        db_healthy_files = stats_query[6] or 0
        db_marked_as_good = stats_query[7] or 0
        db_warning_files = stats_query[8] or 0
        
        # Files marked as good should be considered healthy
        db_healthy_files = ScanResult.query.filter(
            (ScanResult.is_corrupted == False) | (ScanResult.marked_as_good == True)
        ).count()
        
        # Get monitored paths info from database in a single query
        monitored_paths = []
        total_filesystem_files = db_total_files  # Use DB total since all files are scanned
        
        # Get file counts per path using a single aggregated query
        path_counts_query = db.session.execute(
            text("""
                SELECT 
                    CASE 
                        WHEN file_path LIKE '/movies%' THEN '/movies'
                        WHEN file_path LIKE '/tv%' THEN '/tv'
                        WHEN file_path LIKE '/originals%' THEN '/originals'
                        WHEN file_path LIKE '/immich%' THEN '/immich'
                        ELSE 'other'
                    END as base_path,
                    COUNT(*) as file_count
                FROM scan_results
                GROUP BY base_path
            """)
        ).fetchall()
        
        # Convert to dictionary for easy lookup
        path_counts = {row[0]: row[1] for row in path_counts_query}
        
        # Build monitored paths info
        for path in scan_paths:
            path_info = {
                'path': path,
                'exists': True,  # Assume exists since we have DB data
                'is_directory': True,
                'last_checked': datetime.now(timezone.utc).isoformat(),
                'file_count': path_counts.get(path, 0),
                'error': None
            }
            monitored_paths.append(path_info)
        
        # Get most recent scan information
        latest_scan = ScanResult.query.order_by(ScanResult.scan_date.desc()).first()
        latest_scan_date = latest_scan.scan_date.isoformat() if latest_scan and latest_scan.scan_date else None
        
        # Get oldest scan information
        oldest_scan = ScanResult.query.order_by(ScanResult.scan_date.asc()).first()
        oldest_scan_date = oldest_scan.scan_date.isoformat() if oldest_scan and oldest_scan.scan_date else None
        
        # Calculate scanning performance metrics using SQL aggregation
        avg_scan_time_result = db.session.execute(
            text("""
                SELECT AVG(scan_duration) as avg_duration, COUNT(*) as count
                FROM scan_results 
                WHERE scan_duration IS NOT NULL AND scan_duration > 0
            """)
        ).fetchone()
        avg_scan_time = avg_scan_time_result[0] if avg_scan_time_result and avg_scan_time_result[0] else 0
        
        # Get scan configuration
        scan_config = {
            'scan_paths': scan_paths,
            'max_files_to_scan': max_files_to_scan,
            'max_workers': max_workers,
            'app_timezone': APP_TIMEZONE
        }
        
        # Current scan status
        current_scan_status = {
            'is_scanning': scan_state.get('is_scanning', False),
            'phase': scan_state.get('phase', 'idle'),
            'files_processed': scan_state.get('files_processed', 0),
            'estimated_total': scan_state.get('estimated_total', 0),
            'current_file': scan_state.get('current_file'),
            'start_time': scan_state.get('start_time').isoformat() if scan_state.get('start_time') else None
        }
        
        response = {
            'total_files_found': total_filesystem_files,
            'monitored_paths': monitored_paths,
            'database_stats': {
                'total_files': db_total_files,
                'completed_files': db_completed_files,
                'pending_files': db_pending_files,
                'scanning_files': db_scanning_files,
                'error_files': db_error_files,
                'corrupted_files': db_corrupted_files,
                'healthy_files': db_healthy_files,
                'marked_as_good': db_marked_as_good,
                'warning_files': db_warning_files
            },
            'scan_performance': {
                'latest_scan_date': latest_scan_date,
                'oldest_scan_date': oldest_scan_date,
                'average_scan_time_seconds': round(avg_scan_time, 2),
                'total_scans_with_duration': avg_scan_time_result[1] if avg_scan_time_result else 0
            },
            'scan_configuration': scan_config,
            'current_scan_status': current_scan_status,
            'system_info': {
                'version': __version__,
                'github_url': __github_url__,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'uptime_check': datetime.now(timezone.utc).isoformat()
            }
        }
        
        execution_time = time.time() - start_time
        logger.info(f"System info response prepared in {execution_time:.2f}s - DB files: {db_total_files}, Paths: {len(monitored_paths)}")
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error getting system info: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/scan-status')
def get_scan_status():
    """Get current scan status based on database state and scan thread state"""
    try:
        # Check if any files are currently being scanned
        scanning_files = ScanResult.query.filter_by(scan_status='scanning').count()
        pending_files = ScanResult.query.filter_by(scan_status='pending').count()
        completed_files = ScanResult.query.filter_by(scan_status='completed').count()
        
        # Get thread-safe copy of scan state
        state = get_scan_state_copy()
        
        # Only consider scanning if scan thread is actually active
        # Don't show progress bar just because there are pending files
        is_scanning = state.get('scan_thread_active', False) or state.get('is_scanning', False)
        
        logger.info(f"Scan status requested - Is scanning: {is_scanning} (scanning: {scanning_files}, pending: {pending_files}, thread_active: {state.get('scan_thread_active', False)})")
        logger.info(f"Scan state details - phase: {state.get('phase')}, phase_number: {state.get('phase_number')}, phase_current: {state.get('phase_current')}, phase_total: {state.get('phase_total')}")
        
        # Detect and log stuck scan state
        if state.get('scan_thread_active') and state.get('phase') == 'scanning':
            phase_current = state.get('phase_current', 0)
            phase_total = state.get('phase_total', 0)
            if phase_total > 0 and phase_current >= phase_total:
                logger.error(f"STUCK SCAN DETECTED: phase_current ({phase_current}) >= phase_total ({phase_total})")
        
        # Calculate ETA if we have scan time data
        eta_seconds = 0
        current_file_progress = ""
        
        if state['is_scanning'] and state['average_scan_time'] > 0:
            remaining_files = state['estimated_total'] - state['files_processed']
            eta_seconds = remaining_files * state['average_scan_time']
            
            # Current file progress
            if state['current_file'] and state['current_file_start_time']:
                current_duration = (datetime.now() - state['current_file_start_time']).total_seconds()
                file_name = state['current_file'].split('/')[-1]
                current_file_progress = f"Scanning: {file_name} ({current_duration:.0f}s)"

        # Calculate phase progress correctly
        phase_current = state.get('phase_current', 0)
        phase_total = state.get('phase_total', 0)
        
        # For scanning phase, ensure we're tracking files processed correctly
        if state.get('phase') == 'scanning' and state.get('is_scanning'):
            phase_current = state.get('files_processed', 0)
            phase_total = state.get('estimated_total', pending_files)

        response = {
            'is_scanning': is_scanning,
            'files_processed': state['files_processed'] if state['is_scanning'] else 0,
            'estimated_total': state['estimated_total'] if state['is_scanning'] else pending_files + scanning_files,
            'scanning_files': scanning_files,
            'pending_files': pending_files,
            'completed_files': completed_files,
            'phase': state.get('phase', 'idle'),
            'current_file': state.get('current_file'),
            'current_file_progress': current_file_progress,
            'average_scan_time': state.get('average_scan_time', 0),
            'eta_seconds': eta_seconds,
            'progress_message': state.get('progress_message', ''),
            # Phase-specific progress with correct values
            'phase_current': phase_current,
            'phase_total': phase_total,
            'phase_number': state.get('phase_number', 0),
            'total_phases': state.get('total_phases', 3),
            'discovery_count': state.get('discovery_count', 0),
            'adding_progress': state.get('adding_progress', 0)
        }
        
        if state['start_time'] and state['is_scanning']:
            response['duration'] = (datetime.now() - state['start_time']).total_seconds()
            response['start_time'] = state['start_time'].isoformat()
        
        return jsonify(response)
    except Exception as e:
        logger.error(f"Error getting scan status: {str(e)}")
        return jsonify({
            'is_scanning': False,
            'files_processed': 0,
            'estimated_total': 0,
            'error': str(e)
        })

@app.route('/api/test-cleanup')
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

@app.route('/api/cleanup-status')
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
                'total_phases': 2,
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
                'total_phases': 2,
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
            
            # Calculate progress percentage based on current phase
            # For cleanup, the checking phase is the majority of the work (checking all files)
            # The deleting phase is usually much smaller (only orphaned files)
            if cleanup_record.phase == 'checking' and cleanup_record.total_files > 0:
                # Checking phase is weighted as 90% of total progress
                response['progress_percentage'] = (cleanup_record.files_processed / cleanup_record.total_files) * 90
            elif cleanup_record.phase == 'deleting' and cleanup_record.phase_total > 0:
                # Deleting phase is the remaining 10%
                response['progress_percentage'] = 90 + (cleanup_record.phase_current / cleanup_record.phase_total) * 10
            elif cleanup_record.phase == 'complete':
                response['progress_percentage'] = 100
            else:
                response['progress_percentage'] = 0
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error getting cleanup status: {str(e)}")
        return jsonify({
            'is_running': False,
            'phase': 'error',
            'error': str(e)
        })

@app.route('/api/file-changes-status')
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
            
            # Calculate progress percentage based on current phase
            if file_changes_record.phase == 'starting':
                response['progress_percentage'] = 5
            elif file_changes_record.phase == 'checking_hashes' and file_changes_record.total_files > 0:
                # Phase 2 is 80% of total progress
                response['progress_percentage'] = 5 + (file_changes_record.files_processed / file_changes_record.total_files) * 80
            elif file_changes_record.phase == 'verifying_changes' and file_changes_record.phase_total > 0:
                # Phase 3 is the remaining 15%
                response['progress_percentage'] = 85 + (file_changes_record.phase_current / file_changes_record.phase_total) * 15
            elif file_changes_record.phase == 'complete':
                response['progress_percentage'] = 100
            else:
                response['progress_percentage'] = 0
            
            # Include results if check is complete
            if file_changes_record.phase == 'complete' and file_changes_record.changed_files:
                response['result'] = {
                    'changed_files': json.loads(file_changes_record.changed_files),
                    'total_checked': file_changes_record.total_files,
                    'corrupted_found': file_changes_record.corrupted_found
                }
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error getting file changes status: {str(e)}")
        return jsonify({
            'is_running': False,
            'phase': 'error',
            'error': str(e)
        })

@app.route('/api/cancel-scan', methods=['POST'])
def cancel_scan():
    """Cancel running scan operation"""
    try:
        with scan_state_lock:
            if not scan_state['is_scanning']:
                return jsonify({'error': 'No scan operation is currently running'}), 400
            
            scan_state['cancel_requested'] = True
            logger.info("Scan cancellation requested")
        
        return jsonify({
            'message': 'Scan cancellation requested',
            'status': 'cancelling'
        })
        
    except Exception as e:
        logger.error(f"Error cancelling scan: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/cancel-cleanup', methods=['POST'])
def cancel_cleanup():
    """Cancel running cleanup operation"""
    try:
        # Find active cleanup operation
        active_cleanup = CleanupState.query.filter_by(is_active=True).first()
        if not active_cleanup:
            return jsonify({'error': 'No cleanup operation is currently running'}), 400
        
        # Mark as cancelled - the async thread will check this
        active_cleanup.is_active = False
        active_cleanup.phase = 'cancelled' 
        active_cleanup.progress_message = 'Cancellation requested'
        db.session.commit()
        logger.info(f"Cleanup cancellation requested for ID: {active_cleanup.cleanup_id}")
        
        return jsonify({
            'message': 'Cleanup cancellation requested',
            'status': 'cancelling'
        })
        
    except Exception as e:
        logger.error(f"Error cancelling cleanup: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/reset-cleanup-state', methods=['POST'])
def reset_cleanup_state():
    """Reset stuck cleanup state - use when cleanup state is stuck after restart"""
    try:
        # Find any active cleanup operations
        active_cleanups = CleanupState.query.filter_by(is_active=True).all()
        
        if not active_cleanups:
            return jsonify({'message': 'No active cleanup operations found'}), 200
        
        # Mark all active cleanups as inactive
        for cleanup in active_cleanups:
            cleanup.is_active = False
            cleanup.phase = 'interrupted'
            cleanup.progress_message = 'Reset due to stuck state'
            logger.info(f"Resetting stuck cleanup state for ID: {cleanup.cleanup_id}")
        
        db.session.commit()
        
        return jsonify({
            'message': f'Reset {len(active_cleanups)} stuck cleanup operations',
            'count': len(active_cleanups)
        })
        
    except Exception as e:
        logger.error(f"Error resetting cleanup state: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/cancel-file-changes', methods=['POST'])
def cancel_file_changes():
    """Cancel running file changes check operation"""
    try:
        # Find active file changes operation
        active_check = FileChangesState.query.filter_by(is_active=True).first()
        if not active_check:
            return jsonify({'error': 'No file changes check is currently running'}), 400
        
        # Mark as cancelled - the async thread will check this
        active_check.is_active = False
        active_check.phase = 'cancelled' 
        active_check.progress_message = 'Cancellation requested'
        db.session.commit()
        logger.info(f"File changes check cancellation requested for ID: {active_check.check_id}")
        
        return jsonify({
            'message': 'File changes check cancellation requested',
            'status': 'cancelling'
        })
        
    except Exception as e:
        logger.error(f"Error cancelling file changes check: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/export-csv', methods=['GET', 'POST'])
def export_csv():
    try:
        # Determine export type and get appropriate results
        if request.method == 'POST':
            data = request.get_json() or {}
            file_ids = data.get('file_ids', [])
            
            if file_ids:
                # Export selected files
                results = ScanResult.query.filter(ScanResult.id.in_(file_ids)).all()
                export_type = "selected"
                logger.info(f"Exporting {len(results)} selected scan results to CSV")
            else:
                # Export based on current filter and search
                filter_type = data.get('filter', 'all')
                search = data.get('search', '')
                
                query = ScanResult.query
                
                # Apply search filter
                if search:
                    query = query.filter(ScanResult.file_path.contains(search))
                
                # Apply corruption filter
                if filter_type == 'corrupted':
                    # Show only corrupted files that don't have warnings and aren't marked as good
                    query = query.filter(
                        (ScanResult.is_corrupted == True) & 
                        ((ScanResult.has_warnings == False) | (ScanResult.has_warnings == None)) &
                        (ScanResult.marked_as_good == False)
                    )
                elif filter_type == 'healthy':
                    # Show only healthy files (no corruption, no warnings, or marked as good)
                    query = query.filter(
                        ((ScanResult.is_corrupted == False) & 
                         ((ScanResult.has_warnings == False) | (ScanResult.has_warnings == None))) |
                        (ScanResult.marked_as_good == True)
                    )
                elif filter_type == 'warning':
                    # Show files with warnings that aren't marked as good
                    query = query.filter(
                        (ScanResult.has_warnings == True) &
                        (ScanResult.marked_as_good == False)
                    )
                # 'all' filter - no additional filtering needed
                
                results = query.all()
                export_type = filter_type if filter_type != 'all' else 'all'
                logger.info(f"Exporting {len(results)} scan results to CSV (filter: {filter_type}, search: '{search}')")
        else:
            # GET request - export all files
            results = ScanResult.query.all()
            export_type = "all"
            logger.info(f"Exporting {len(results)} scan results to CSV (all results via GET)")
        
        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write CSV header
        writer.writerow([
            'ID',
            'File Path',
            'File Size (bytes)',
            'File Type',
            'Creation Date',
            'Is Corrupted',
            'Corruption Details',
            'Scan Date',
            'Scan Status',
            'Discovered Date',
            'Marked as Good'
        ])
        
        # Write data rows
        for result in results:
            writer.writerow([
                result.id,
                result.file_path,
                result.file_size or 0,
                result.file_type or 'Unknown',
                result.creation_date.isoformat() if result.creation_date else '',
                'Yes' if result.is_corrupted else 'No',
                result.corruption_details or '',
                result.scan_date.isoformat() if result.scan_date else '',
                getattr(result, 'scan_status', 'completed'),  # Default to completed for old records
                getattr(result, 'discovered_date', result.scan_date).isoformat() if getattr(result, 'discovered_date', result.scan_date) else '',
                'Yes' if result.marked_as_good else 'No'
            ])
        
        # Prepare response
        output.seek(0)
        csv_content = output.getvalue()
        output.close()
        
        # Create filename with timestamp and export type
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"pixelprobe_{export_type}_{timestamp}.csv"
        
        logger.info(f"CSV export completed - {len(results)} records exported to {filename}")
        
        # Return CSV file
        return send_file(
            io.BytesIO(csv_content.encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logger.error(f"Error exporting CSV: {str(e)}")
        return jsonify({'error': f'Export failed: {str(e)}'}), 500

def create_tables():
    """Initialize database tables with migration support"""
    with app.app_context():
        try:
            # First, create all tables if they don't exist
            db.create_all()
            logger.info("Database tables created/verified successfully")
            
            # Perform migrations
            migrate_database()
            
        except Exception as e:
            logger.error(f"Error in database initialization: {str(e)}")


def check_file_changes_async(check_id):
    """Async function to check file changes with progress tracking"""
    import concurrent.futures
    import signal
    from functools import partial
    
    with app.app_context():
        try:
            # Get the file changes state record
            file_changes_record = FileChangesState.query.filter_by(check_id=check_id).first()
            if not file_changes_record:
                logger.error(f"File changes state record not found for check_id: {check_id}")
                return
            
            # Phase 1: Starting
            file_changes_record.phase = 'starting'
            file_changes_record.phase_number = 1
            file_changes_record.progress_message = 'Phase 1 of 3: Starting file changes check...'
            db.session.commit()
            
            # Get total count of files
            total_files = ScanResult.query.count()
            logger.info(f"File change check starting - found {total_files} total files in database")
            
            # Phase 2: Checking hashes
            file_changes_record.phase = 'checking_hashes'
            file_changes_record.phase_number = 2
            file_changes_record.total_files = total_files
            file_changes_record.phase_total = total_files
            file_changes_record.progress_message = f'Phase 2 of 3: Checking {total_files} files for hash changes...'
            db.session.commit()
            
            batch_size = 1000  # Process files in batches to handle large databases
            total_batches = (total_files + batch_size - 1) // batch_size
            
            checker = initialize_media_checker()
            changed_files = []
            
            # Helper function to process a single file with timeout
            def process_file_for_changes(file_data):
                """Process a single file to check for changes"""
                file_path = file_data['file_path']
                stored_hash = file_data['stored_hash']
                stored_modified = file_data['stored_modified']
                
                # Update current file when starting to process
                with app.app_context():
                    file_changes_record = FileChangesState.query.filter_by(check_id=check_id).first()
                    if file_changes_record:
                        file_changes_record.current_file = file_path
                        db.session.commit()
                
                try:
                    # Check if file exists
                    if not os.path.exists(file_path):
                        return {
                            'file_path': file_path,
                            'change_type': 'deleted',
                            'stored_hash': stored_hash,
                            'current_hash': None
                        }
                    
                    # Calculate hash - no timeout, we want to check all files
                    start_time = time.time()
                    current_hash = checker.calculate_file_hash(file_path)
                    hash_time = time.time() - start_time
                    
                    # Log if file took a long time but don't skip it
                    if hash_time > 60:  # Log files that take more than 1 minute
                        logger.info(f"Large file processed: {file_path} took {hash_time:.1f}s")
                    
                    # Get current modification time
                    current_stats = os.stat(file_path)
                    current_modified = datetime.fromtimestamp(current_stats.st_mtime)
                    
                    # Check if hash has changed
                    if current_hash != stored_hash:
                        return {
                            'file_path': file_path,
                            'change_type': 'modified',
                            'stored_hash': stored_hash,
                            'current_hash': current_hash,
                            'stored_modified': stored_modified.isoformat() if stored_modified else None,
                            'current_modified': current_modified.isoformat()
                        }
                    
                    return None  # No change
                    
                except Exception as e:
                    logger.error(f"Error processing file {file_path}: {str(e)}")
                    return None
            
            # Process files in batches with parallel processing
            with concurrent.futures.ThreadPoolExecutor(max_workers=checker.max_workers) as executor:
                for batch_num in range(total_batches):
                    # Check for cancellation
                    with file_changes_state_lock:
                        if file_changes_state['cancel_requested']:
                            logger.info("File changes check cancelled by user")
                            file_changes_state['phase'] = 'cancelled'
                            file_changes_state['progress_message'] = 'Operation cancelled'
                            file_changes_state['is_running'] = False
                            return
                        
                        file_changes_state['batch_number'] = batch_num + 1
                        file_changes_state['batch_start_time'] = time.time()
                    
                    offset = batch_num * batch_size
                    
                    # Get batch of records
                    batch_results = ScanResult.query.order_by(ScanResult.id).offset(offset).limit(batch_size).all()
                    
                    if not batch_results:
                        break
                    
                    # Log batch progress
                    logger.info(f"Processing batch {batch_num + 1} of {total_batches} (offset: {offset}, size: {len(batch_results)})")
                    
                    # Prepare file data for parallel processing
                    file_data_list = []
                    for result in batch_results:
                        file_data_list.append({
                            'file_path': result.file_path,
                            'stored_hash': result.file_hash,
                            'stored_modified': result.last_modified
                        })
                    
                    # Process files in parallel
                    futures = []
                    future_to_file_data = {}  # Map futures to file data
                    for file_data in file_data_list:
                        future = executor.submit(process_file_for_changes, file_data)
                        futures.append(future)
                        future_to_file_data[future] = file_data
                    
                    # Collect results
                    batch_changed_files = []
                    files_completed_in_batch = 0
                    for future in concurrent.futures.as_completed(futures):
                        # Check for cancellation during batch processing
                        with file_changes_state_lock:
                            if file_changes_state['cancel_requested']:
                                # Cancel remaining futures
                                for f in futures:
                                    f.cancel()
                                logger.info("File changes check cancelled by user during batch processing")
                                file_changes_state['phase'] = 'cancelled'
                                file_changes_state['progress_message'] = 'Operation cancelled'
                                file_changes_state['is_running'] = False
                                return
                        
                        file_data = future_to_file_data[future]
                        file_path = file_data['file_path']
                        
                        try:
                            # Wait for result without timeout - we want to check all files
                            result = future.result()
                            if result:
                                batch_changed_files.append(result)
                        except Exception as e:
                            logger.error(f"Error processing file {file_path}: {str(e)}")
                        
                        # Update progress
                        files_completed_in_batch += 1
                        with file_changes_state_lock:
                            files_done = offset + files_completed_in_batch
                            file_changes_state['files_processed'] = files_done
                            file_changes_state['phase_current'] = files_done
                            
                            # Calculate ETA
                            elapsed_time = time.time() - file_changes_state['start_time']
                            if files_done > 0:
                                avg_time_per_file = elapsed_time / files_done
                                remaining_files = total_files - files_done
                                eta_seconds = remaining_files * avg_time_per_file
                                eta_minutes = int(eta_seconds / 60)
                                eta_hours = int(eta_minutes / 60)
                                eta_minutes = eta_minutes % 60
                                
                                if eta_hours > 0:
                                    eta_str = f"{eta_hours}h {eta_minutes}m"
                                else:
                                    eta_str = f"{eta_minutes}m"
                                
                                file_changes_state['progress_message'] = f'Batch {batch_num + 1}/{total_batches}: Checking files... ETA: {eta_str}'
                                file_changes_state['progress_percentage'] = (files_done / total_files) * 100 if total_files > 0 else 0
                        
                        # Update database progress more frequently (every 5 files or at end of batch)
                        # This ensures progress is visible even if some files timeout
                        if files_completed_in_batch % 5 == 0 or files_completed_in_batch == len(batch_results):
                            with app.app_context():
                                file_changes_record = FileChangesState.query.filter_by(check_id=check_id).first()
                                if file_changes_record:
                                    file_changes_record.files_processed = files_done
                                    file_changes_record.phase_current = files_done
                                    file_changes_record.changes_found = len(changed_files)
                                    file_changes_record.progress_message = file_changes_state['progress_message']
                                    db.session.commit()
                                    logger.info(f"Updated database: files_processed={files_done}, batch={batch_num + 1}, files_in_batch={files_completed_in_batch}")
                    
                    # Add batch results to overall results
                    changed_files.extend(batch_changed_files)
                    
                    with file_changes_state_lock:
                        file_changes_state['changes_found'] = len(changed_files)
                    
                    # Force database update at end of each batch to ensure progress is saved
                    with app.app_context():
                        file_changes_record = FileChangesState.query.filter_by(check_id=check_id).first()
                        if file_changes_record:
                            files_done = offset + files_completed_in_batch
                            file_changes_record.files_processed = files_done
                            file_changes_record.phase_current = files_done
                            file_changes_record.changes_found = len(changed_files)
                            file_changes_record.progress_message = file_changes_state.get('progress_message', '')
                            db.session.commit()
                            logger.info(f"Batch {batch_num + 1} complete - Updated database: files_processed={files_done}")
                    
                    # Log batch completion
                    batch_time = time.time() - file_changes_state['batch_start_time']
                    logger.info(f"Completed batch {batch_num + 1} of {total_batches} in {batch_time:.1f}s - found {len(batch_changed_files)} changes")
            
            # Final check for cancellation
            with file_changes_state_lock:
                if file_changes_state['cancel_requested']:
                    return
            
            logger.info(f"Phase 2 complete: Checked {total_files} files, found {len(changed_files)} changes")
            
            # Phase 3: Verify changed files for corruption
            corrupted_count = 0
            
            # Update database for Phase 3
            with app.app_context():
                file_changes_record = FileChangesState.query.filter_by(check_id=check_id).first()
                if file_changes_record:
                    file_changes_record.phase = 'verifying_changes'
                    file_changes_record.phase_number = 3
                    file_changes_record.phase_current = 0
                    file_changes_record.phase_total = len(changed_files) if changed_files else 1  # At least 1 to show progress
                    file_changes_record.changes_found = len(changed_files)
                    file_changes_record.progress_message = f'Phase 3 of 3: Verifying {len(changed_files)} changed files for corruption...'
                    db.session.commit()
            
            with file_changes_state_lock:
                file_changes_state['phase'] = 'verifying_changes'
                file_changes_state['phase_number'] = 3
                file_changes_state['phase_current'] = 0
                file_changes_state['phase_total'] = len(changed_files) if changed_files else 1
                file_changes_state['progress_message'] = f'Phase 3 of 3: Verifying {len(changed_files)} changed files for corruption...'
            
            if changed_files:
                # Verify each changed file for corruption
                logger.info(f"Starting Phase 3: Verifying {len(changed_files)} changed files for corruption")
                
                for idx, changed_file in enumerate(changed_files):
                    # Check for cancellation
                    with file_changes_state_lock:
                        if file_changes_state['cancel_requested']:
                            logger.info("File changes check cancelled during verification phase")
                            return
                    
                    file_path = changed_file['file_path']
                    
                    # Update progress
                    with file_changes_state_lock:
                        file_changes_state['phase_current'] = idx + 1
                        file_changes_state['current_file'] = file_path
                        file_changes_state['progress_message'] = f'Phase 3 of 3: Verifying file {idx + 1}/{len(changed_files)} for corruption...'
                    
                    # Update database progress
                    with app.app_context():
                        file_changes_record = FileChangesState.query.filter_by(check_id=check_id).first()
                        if file_changes_record:
                            file_changes_record.phase_current = idx + 1
                            file_changes_record.current_file = file_path
                            file_changes_record.progress_message = f'Phase 3 of 3: Verifying file {idx + 1}/{len(changed_files)} for corruption...'
                            db.session.commit()
                    
                    # Scan the changed file for corruption
                    try:
                        scan_result = checker.scan_file(file_path, deep_scan=False)
                        if scan_result.get('is_corrupted'):
                            corrupted_count += 1
                            changed_file['is_corrupted'] = True
                            changed_file['corruption_details'] = scan_result.get('corruption_details')
                            logger.warning(f"Changed file is corrupted: {file_path}")
                        else:
                            changed_file['is_corrupted'] = False
                    except Exception as e:
                        logger.error(f"Error scanning changed file {file_path}: {str(e)}")
                        changed_file['scan_error'] = str(e)
                    
                    # Update corrupted count in state
                    with file_changes_state_lock:
                        file_changes_state['corrupted_found'] = corrupted_count
                    
                    # Update database with corrupted count
                    with app.app_context():
                        file_changes_record = FileChangesState.query.filter_by(check_id=check_id).first()
                        if file_changes_record:
                            file_changes_record.corrupted_found = corrupted_count
                            db.session.commit()
            else:
                # No files changed - still show Phase 3 progress for smooth transition
                logger.info("No files changed, completing Phase 3")
                
                # Update progress to show Phase 3 completion
                with file_changes_state_lock:
                    file_changes_state['phase_current'] = 1
                    file_changes_state['phase_total'] = 1
                    file_changes_state['progress_message'] = 'Phase 3 of 3: No changed files to verify'
                
                # Update database
                with app.app_context():
                    file_changes_record = FileChangesState.query.filter_by(check_id=check_id).first()
                    if file_changes_record:
                        file_changes_record.phase_current = 1
                        file_changes_record.phase_total = 1
                        file_changes_record.progress_message = 'Phase 3 of 3: No changed files to verify'
                        db.session.commit()
                
                # Small delay to show Phase 3 progress
                time.sleep(0.5)
            
            logger.info(f"File change check complete: Checked {total_files} files, found {len(changed_files)} changes, {corrupted_count} corrupted")
            
            # Mark as complete and store results
            with file_changes_state_lock:
                file_changes_state['phase'] = 'complete'
                file_changes_state['progress_message'] = f'Check complete: {len(changed_files)} files changed, {corrupted_count} corrupted'
                file_changes_state['progress_percentage'] = 100  # Ensure 100% on completion
                file_changes_state['files_processed'] = total_files  # Ensure counts match
                file_changes_state['phase_current'] = len(changed_files) if changed_files else 1
                file_changes_state['phase_total'] = len(changed_files) if changed_files else 1
                file_changes_state['corrupted_found'] = corrupted_count
                # Store results for retrieval
                file_changes_state['result'] = {
                    'changed_files': changed_files,
                    'total_checked': total_files,
                    'corrupted_found': corrupted_count
                }
                # Set is_running to False AFTER setting all completion data
                file_changes_state['is_running'] = False
            
            # Update database with final results
            with app.app_context():
                file_changes_record = FileChangesState.query.filter_by(check_id=check_id).first()
                if file_changes_record:
                    file_changes_record.phase = 'complete'
                    file_changes_record.is_active = False
                    file_changes_record.end_time = datetime.now(timezone.utc)
                    file_changes_record.files_processed = total_files
                    file_changes_record.phase_current = len(changed_files) if changed_files else 1
                    file_changes_record.phase_total = len(changed_files) if changed_files else 1
                    file_changes_record.corrupted_found = corrupted_count
                    file_changes_record.progress_message = f'Check complete: {len(changed_files)} files changed, {corrupted_count} corrupted'
                    file_changes_record.changed_files = json.dumps(changed_files)
                    db.session.commit()
            
        except Exception as e:
            logger.error(f"Error in async file changes check: {str(e)}")
            with file_changes_state_lock:
                file_changes_state['phase'] = 'error'
                file_changes_state['progress_message'] = f'Error: {str(e)}'
                file_changes_state['is_running'] = False

@app.route('/api/file-changes', methods=['GET', 'POST'])
def check_file_changes():
    """Start async check for file changes"""
    # Check if file changes check is already running
    active_check = FileChangesState.query.filter_by(is_active=True).first()
    if active_check:
        return jsonify({'error': 'File changes check already in progress'}), 409
    
    # Create new file changes state record
    import uuid
    check_id = str(uuid.uuid4())
    
    file_changes_record = FileChangesState(
        check_id=check_id,
        is_active=True,
        phase='starting',
        phase_number=1,
        start_time=datetime.now(timezone.utc),
        progress_message='Initializing file changes check...'
    )
    db.session.add(file_changes_record)
    db.session.commit()
    
    logger.info(f"Created file changes state with ID: {check_id}")
    
    # Start check in background thread
    check_thread = threading.Thread(target=check_file_changes_async, args=(check_id,))
    check_thread.start()
    
    return jsonify({
        'message': 'File changes check started',
        'check_id': check_id,
        'status': 'started'
    })

def cleanup_orphaned_async(cleanup_id):
    """Async function to clean up orphaned records with progress tracking"""
    logger.info(f"Cleanup async function started with ID: {cleanup_id}")
    with app.app_context():
        try:
            # Get the cleanup state from database
            cleanup_record = CleanupState.query.filter_by(cleanup_id=cleanup_id).first()
            if not cleanup_record:
                logger.error(f"Cleanup state not found for ID: {cleanup_id}")
                return
            
            logger.info("Inside app context, updating state to checking")
            cleanup_record.phase = 'checking'
            cleanup_record.phase_number = 1
            cleanup_record.progress_message = 'Phase 1 of 2: Counting files in database...'
            db.session.commit()
            
            # Get total count of files
            total_files = ScanResult.query.count()
            logger.info(f"Found {total_files} total files to check")
            
            cleanup_record.total_files = total_files
            cleanup_record.phase_total = total_files
            cleanup_record.progress_message = f'Phase 1 of 2: Checking {total_files} files for orphaned records...'
            db.session.commit()
            
            checker = initialize_media_checker()
            orphaned_ids = []  # Store IDs instead of full records to save memory
            orphaned_files = []  # Store file paths for logging
            batch_size = 1000  # Process files in batches to handle large databases
            
            # Process files in batches
            for offset in range(0, total_files, batch_size):
                # Check for cancellation by refreshing from database
                db.session.refresh(cleanup_record)
                if not cleanup_record.is_active:
                    logger.info("Cleanup cancelled by user")
                    cleanup_record.phase = 'cancelled'
                    cleanup_record.progress_message = 'Operation cancelled'
                    cleanup_record.end_time = datetime.now(timezone.utc)
                    db.session.commit()
                    return
                
                # Get batch of records
                batch_results = ScanResult.query.order_by(ScanResult.id).offset(offset).limit(batch_size).all()
                
                if not batch_results:
                    break
                
                # Log batch progress
                logger.info(f"Processing batch {offset//batch_size + 1} of {(total_files + batch_size - 1)//batch_size} (offset: {offset}, size: {len(batch_results)})")
                
                # Check each file in the batch
                for result in batch_results:
                    idx = offset + batch_results.index(result)
                    
                    cleanup_record.files_processed = idx + 1
                    cleanup_record.phase_current = idx + 1
                    cleanup_record.current_file = result.file_path
                    db.session.commit()
                    
                    if not os.path.exists(result.file_path):
                        orphaned_ids.append(result.id)
                        orphaned_files.append(result.file_path)
                        cleanup_record.orphaned_found = len(orphaned_ids)
                        db.session.commit()
            
            logger.info(f"Phase 1 complete: Checked {total_files} files, found {len(orphaned_ids)} orphaned records")
            
            # Log the orphaned files
            if len(orphaned_files) > 0:
                logger.info("Orphaned files found:")
                for file_path in orphaned_files[:100]:  # Log first 100 files to avoid huge logs
                    logger.info(f"  - {file_path}")
                if len(orphaned_files) > 100:
                    logger.info(f"  ... and {len(orphaned_files) - 100} more orphaned files")
            
            # Check if we have any orphaned files to delete
            if len(orphaned_ids) > 0:
                # Start deletion phase
                cleanup_record.phase = 'deleting'
                cleanup_record.phase_number = 2
                cleanup_record.phase_current = 0
                cleanup_record.phase_total = len(orphaned_ids)
                cleanup_record.progress_message = f'Phase 2 of 2: Deleting {len(orphaned_ids)} orphaned records...'
                db.session.commit()
                
                # Delete orphaned records in batches using IDs
                deletion_batch_size = 1000
                for i in range(0, len(orphaned_ids), deletion_batch_size):
                    # Check for cancellation
                    db.session.refresh(cleanup_record)
                    if not cleanup_record.is_active:
                        logger.info("Cleanup cancelled during deletion phase")
                        cleanup_record.phase = 'cancelled'
                        cleanup_record.progress_message = f'Operation cancelled (deleted {i} of {len(orphaned_ids)} records)'
                        cleanup_record.end_time = datetime.now(timezone.utc)
                        db.session.commit()
                        return
                    
                    batch_ids = orphaned_ids[i:i + deletion_batch_size]
                    # Use bulk delete for efficiency
                    ScanResult.query.filter(ScanResult.id.in_(batch_ids)).delete(synchronize_session=False)
                    db.session.commit()
                    
                    deleted_so_far = min(i + deletion_batch_size, len(orphaned_ids))
                    cleanup_record.phase_current = deleted_so_far
                    cleanup_record.progress_message = f'Phase 2 of 2: Deleted {deleted_so_far} of {len(orphaned_ids)} records...'
                    db.session.commit()
            else:
                # No orphaned files found, still show Phase 2 completion
                cleanup_record.phase = 'deleting'
                cleanup_record.phase_number = 2
                cleanup_record.phase_current = 0
                cleanup_record.phase_total = 0
                cleanup_record.progress_message = 'Phase 2 of 2: No orphaned records to delete'
                db.session.commit()
                
                # Give UI time to show Phase 2 status
                time.sleep(1)
            
            # Mark as complete
            cleanup_record.phase = 'complete'
            cleanup_record.progress_message = f'Successfully removed {len(orphaned_ids)} orphaned records'
            cleanup_record.is_active = False
            cleanup_record.orphaned_found = len(orphaned_ids)
            cleanup_record.end_time = datetime.now(timezone.utc)
            db.session.commit()
            
            logger.info(f"Cleaned up {len(orphaned_ids)} orphaned records")
            logger.info(f"Total size of orphaned files cleaned: {len(orphaned_files)} files")
            
        except Exception as e:
            logger.error(f"Error in async cleanup: {str(e)}", exc_info=True)
            if cleanup_record:
                cleanup_record.phase = 'error'
                cleanup_record.error_message = str(e)
                cleanup_record.progress_message = f'Error: {str(e)}'
                cleanup_record.is_active = False
                cleanup_record.end_time = datetime.now(timezone.utc)
                db.session.commit()

@app.route('/api/cleanup-orphaned', methods=['POST'])
def cleanup_orphaned_records():
    """Start async cleanup of orphaned records"""
    logger.info("Cleanup orphaned endpoint called")
    
    # Check if cleanup is already running
    active_cleanup = CleanupState.query.filter_by(is_active=True).first()
    if active_cleanup:
        logger.warning("Cleanup already in progress")
        return jsonify({'error': 'Cleanup operation already in progress'}), 409
    
    # Create new cleanup state record
    import uuid
    cleanup_id = str(uuid.uuid4())
    cleanup_record = CleanupState(
        cleanup_id=cleanup_id,
        is_active=True,
        phase='starting',
        start_time=datetime.now(timezone.utc),
        progress_message='Starting cleanup operation...',
        files_processed=0,
        total_files=0,
        orphaned_found=0
    )
    db.session.add(cleanup_record)
    db.session.commit()
    
    logger.info(f"Starting cleanup thread with ID: {cleanup_id}")
    
    # Start cleanup in background thread
    try:
        cleanup_thread = threading.Thread(target=cleanup_orphaned_async, args=(cleanup_id,))
        cleanup_thread.start()
        logger.info("Cleanup thread started successfully")
    except Exception as e:
        logger.error(f"Failed to start cleanup thread: {str(e)}")
        cleanup_record.is_active = False
        cleanup_record.phase = 'error'
        cleanup_record.error_message = str(e)
        db.session.commit()
        return jsonify({'error': f'Failed to start cleanup: {str(e)}'}), 500
    
    logger.info("Cleanup thread started, returning status=started")
    
    return jsonify({
        'message': 'Cleanup operation started',
        'status': 'started'
    })

@app.route('/api/scan-parallel', methods=['POST'])
def scan_files_parallel():
    """Mark files as pending and start a targeted rescan"""
    data = request.get_json()
    file_paths = data.get('file_paths', [])
    deep_scan = data.get('deep_scan', False)
    
    if not file_paths:
        return jsonify({'error': 'No file paths provided'}), 400
    
    # Check if a scan is already in progress
    active_scan = ScanState.query.filter_by(is_active=True).first()
    if active_scan or scan_state['is_scanning']:
        logger.warning("Scan already in progress, rejecting rescan request")
        return jsonify({'error': 'Scan already in progress'}), 409
    
    try:
        scan_type = "deep scan" if deep_scan else "rescan"
        logger.info(f"{scan_type.title()} requested for {len(file_paths)} files")
        
        # Mark files as pending in batches
        batch_size = 500
        reset_count = 0
        
        for i in range(0, len(file_paths), batch_size):
            batch = file_paths[i:i + batch_size]
            
            # Use bulk update to reset files to pending and set deep_scan flag
            updated = db.session.query(ScanResult).filter(
                ScanResult.file_path.in_(batch)
            ).update({
                'scan_status': 'pending',
                'scan_date': None,
                'corruption_details': None,
                'is_corrupted': None,
                'scan_tool': None,
                'scan_duration': None,
                'scan_output': None,
                'has_warnings': None,
                'warning_details': None,
                'deep_scan': deep_scan  # Set the deep scan flag
            }, synchronize_session=False)
            
            db.session.commit()
            reset_count += updated
            logger.info(f"Reset batch {i//batch_size + 1}: {updated} files marked as pending (deep_scan={deep_scan})")
        
        logger.info(f"Successfully marked {reset_count} files as pending for {scan_type}")
        
        # Create scan state
        import uuid
        scan_id = str(uuid.uuid4())
        
        # Queue the scan state creation
        db_write_queue.put({
            'type': 'create_scan_state',
            'data': {
                'scan_id': scan_id,
                'is_active': True,
                'phase': 'scanning',
                'start_time': datetime.now(timezone.utc),
                'progress_message': f'Starting {scan_type} of {reset_count} files...'
            }
        })
        
        # Set initial scan state - jump directly to scanning phase
        update_scan_state(
            is_scanning=True,
            start_time=datetime.now(),
            files_processed=0,
            estimated_total=reset_count,
            scan_thread_active=True,
            phase='scanning',
            current_file=None,
            progress_message=f'Starting {scan_type} of {reset_count} files...',
            scan_id=scan_id,
            phase_number=3,  # Jump straight to scanning phase
            total_phases=3,
            phase_current=0,
            phase_total=reset_count
        )
        
        # Start stats update thread to monitor progress
        start_stats_update_thread()
        
        def run_targeted_rescan():
            with app.app_context():
                try:
                    logger.info(f"=== TARGETED {scan_type.upper()} STARTED FOR {reset_count} FILES ===")
                    
                    # Initialize checker
                    checker = initialize_media_checker()
                    
                    # Get all pending file IDs upfront
                    # Memory usage: ~200 bytes per record (ID + path), so 1M files = ~190MB
                    logger.info("Loading pending file IDs...")
                    pending_records = ScanResult.query.filter_by(scan_status='pending').with_entities(ScanResult.id, ScanResult.file_path).all()
                    pending_count = len(pending_records)
                    logger.info(f"Found {pending_count} pending files to scan")
                    
                    # Log memory usage estimate for transparency
                    if pending_count > 100000:
                        estimated_mb = (pending_count * 200) / (1024 * 1024)
                        logger.info(f"Estimated memory usage for ID list: {estimated_mb:.1f} MB")
                    
                    if pending_count == 0:
                        logger.info("No pending files found to scan")
                        update_scan_state(
                            is_scanning=False,
                            scan_thread_active=False,
                            phase='idle',
                            progress_message="No pending files to scan"
                        )
                        return
                    
                    scan_start_time = datetime.now()
                    files_processed = 0
                    files_scanned = 0
                    corrupted_found = 0
                    
                    # Process pending files in batches
                    batch_size = 100  # Smaller batches for better progress tracking
                    
                    # Process files in batches from our ID list
                    for batch_start in range(0, pending_count, batch_size):
                        batch_end = min(batch_start + batch_size, pending_count)
                        batch_records = pending_records[batch_start:batch_end]
                        
                        if not batch_records:
                            break
                        
                        # Get the actual file records for this batch
                        batch_ids = [record.id for record in batch_records]
                        pending_batch = ScanResult.query.filter(ScanResult.id.in_(batch_ids)).all()
                        
                        # Process this batch using ThreadPoolExecutor
                        from concurrent.futures import ThreadPoolExecutor, as_completed
                        
                        with ThreadPoolExecutor(max_workers=checker.max_workers) as executor:
                            # Create mapping of file paths to deep_scan settings
                            file_to_deep_scan = {}
                            valid_files_in_batch = []
                            
                            for record in pending_batch:
                                if os.path.exists(record.file_path):
                                    file_to_deep_scan[record.file_path] = getattr(record, 'deep_scan', False)
                                    valid_files_in_batch.append(record.file_path)
                                else:
                                    # File doesn't exist
                                    logger.warning(f"File no longer exists: {record.file_path}")
                                    record.scan_status = 'error'
                                    record.corruption_details = 'File not found'
                                    db.session.commit()
                                    files_processed += 1
                            
                            if not valid_files_in_batch:
                                continue
                            
                            # Submit scan tasks
                            future_to_file = {
                                executor.submit(checker.scan_file, file_path, file_to_deep_scan.get(file_path, False)): file_path 
                                for file_path in valid_files_in_batch
                            }
                            
                            # Process results as they complete
                            for future in as_completed(future_to_file):
                                file_path = future_to_file[future]
                                try:
                                    result = future.result()
                                    files_processed += 1
                                    files_scanned += 1
                                    
                                    if result.get('is_corrupted'):
                                        corrupted_found += 1
                                        logger.warning(f"CORRUPTED FILE: {file_path} - {result.get('corruption_details')}")
                                    else:
                                        logger.info(f"HEALTHY FILE: {file_path}")
                                    
                                    # Update progress
                                    percentage = (files_processed / pending_count * 100) if pending_count > 0 else 0
                                    file_name = file_path.split('/')[-1] if file_path else ''
                                    update_scan_state(
                                        files_processed=files_processed,
                                        phase_current=files_processed,
                                        current_file=file_path,
                                        progress_message=f"Phase 3 of 3: Scanning {file_name} ({files_processed}/{pending_count} - {percentage:.1f}%)"
                                    )
                                    
                                    # Add scan metadata
                                    result['scan_date'] = datetime.now(timezone.utc)
                                    result['scan_status'] = 'completed'
                                    
                                    # Send to database write queue
                                    db_write_queue.put({
                                        'type': 'batch_update_scan_results',
                                        'results': [result]
                                    })
                                    
                                except Exception as e:
                                    logger.error(f"Error scanning {file_path}: {str(e)}")
                                    files_processed += 1
                                    # Update database with error
                                    error_result = {
                                        'file_path': file_path,
                                        'scan_status': 'error',
                                        'corruption_details': f"Scan error: {str(e)}",
                                        'scan_date': datetime.now(timezone.utc)
                                    }
                                    db_write_queue.put({
                                        'type': 'batch_update_scan_results',
                                        'results': [error_result]
                                    })
                        
                        logger.info(f"Completed batch, processed {files_processed} files total")
                    
                    # Scan completed
                    scan_end_time = datetime.now()
                    scan_duration = scan_end_time - scan_start_time
                    logger.info(f"=== {scan_type.upper()} COMPLETED ===")
                    logger.info(f"Duration: {scan_duration}")
                    logger.info(f"Files processed: {files_processed}")
                    logger.info(f"Files scanned: {files_scanned}")
                    logger.info(f"Corrupted files found: {corrupted_found}")
                    
                    # Update scan state to complete
                    update_scan_state(
                        is_scanning=False,
                        scan_thread_active=False,
                        phase='idle',
                        progress_message=f'{scan_type.title()} completed',
                        files_processed=files_processed
                    )
                    
                    # Mark scan as inactive in database
                    db_write_queue.put({
                        'type': 'update_scan_state',
                        'scan_id': scan_id,
                        'updates': {
                            'is_active': False,
                            'end_time': datetime.now(timezone.utc),
                            'phase': 'completed',
                            'progress_message': f'{scan_type.title()} completed - {files_scanned} files processed, {corrupted_found} corrupted'
                        }
                    })
                    
                except Exception as e:
                    logger.error(f"Error in {scan_type} thread: {str(e)}")
                    update_scan_state(
                        is_scanning=False,
                        scan_thread_active=False,
                        phase='idle',
                        progress_message=f'{scan_type.title()} failed: {str(e)}'
                    )
                    
                    # Mark scan as failed in database
                    db_write_queue.put({
                        'type': 'update_scan_state',
                        'scan_id': scan_id,
                        'updates': {
                            'is_active': False,
                            'end_time': datetime.now(timezone.utc),
                            'phase': 'failed',
                            'progress_message': f'{scan_type.title()} failed: {str(e)}'
                        }
                    })
        
        # Start rescan in background thread
        scan_thread = threading.Thread(target=run_targeted_rescan)
        scan_thread.daemon = True
        scan_thread.start()
        
        return jsonify({
            'message': f'{scan_type.title()} started for {reset_count} files',
            'reset_count': reset_count,
            'scan_id': scan_id
        })
            
    except Exception as e:
        logger.error(f"Error in {scan_type}: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/recover-stuck-scan', methods=['POST'])
def recover_stuck_scan():
    """Emergency recovery endpoint for stuck scans"""
    try:
        with scan_state_lock:
            if not scan_state.get('scan_thread_active'):
                return jsonify({'error': 'No active scan to recover'}), 400
                
            # Force reset scan state
            logger.warning("Forcing recovery of stuck scan")
            scan_state['is_scanning'] = False
            scan_state['scan_thread_active'] = False
            scan_state['phase'] = 'idle'
            scan_state['phase_current'] = 0
            scan_state['phase_total'] = 0
            scan_state['files_processed'] = 0
            
        # Mark any scanning files as pending again
        with app.app_context():
            scanning_files = ScanResult.query.filter_by(scan_status='scanning').all()
            for file in scanning_files:
                file.scan_status = 'pending'
            db.session.commit()
            logger.info(f"Reset {len(scanning_files)} files from 'scanning' to 'pending' status")
            
        # Update scan state in database
        if scan_state.get('scan_id'):
            db_write_queue.put({
                'type': 'update_scan_state_complete',
                'scan_id': scan_state.get('scan_id')
            })
            
        return jsonify({'message': 'Scan recovery initiated'}), 200
        
    except Exception as e:
        logger.error(f"Error recovering stuck scan: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/ignored-patterns')
def get_ignored_patterns():
    """Get all ignored error patterns"""
    try:
        patterns = IgnoredErrorPattern.query.filter_by(is_active=True).all()
        return jsonify({
            'patterns': [pattern.to_dict() for pattern in patterns]
        })
    except Exception as e:
        logger.error(f"Error getting ignored patterns: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/ignored-patterns', methods=['POST'])
def add_ignored_pattern():
    """Add a new ignored error pattern"""
    data = request.get_json()
    pattern = data.get('pattern')
    description = data.get('description', '')
    
    if not pattern:
        return jsonify({'error': 'Pattern is required'}), 400
    
    try:
        # Check if pattern already exists
        existing = IgnoredErrorPattern.query.filter_by(pattern=pattern).first()
        if existing:
            return jsonify({'error': 'Pattern already exists'}), 409
        
        new_pattern = IgnoredErrorPattern(
            pattern=pattern,
            description=description
        )
        db.session.add(new_pattern)
        db.session.commit()
        
        logger.info(f"Added new ignored pattern: {pattern}")
        return jsonify(new_pattern.to_dict()), 201
    except Exception as e:
        logger.error(f"Error adding ignored pattern: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/ignored-patterns/<int:pattern_id>', methods=['DELETE'])
def delete_ignored_pattern(pattern_id):
    """Delete an ignored error pattern"""
    try:
        pattern = IgnoredErrorPattern.query.get_or_404(pattern_id)
        db.session.delete(pattern)
        db.session.commit()
        
        logger.info(f"Deleted ignored pattern: {pattern.pattern}")
        return jsonify({'message': 'Pattern deleted successfully'})
    except Exception as e:
        logger.error(f"Error deleting ignored pattern: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/scan-output/<int:result_id>')
def get_scan_output(result_id):
    """Get full scan output for a specific result"""
    try:
        result = ScanResult.query.get_or_404(result_id)
        return jsonify({
            'file_path': result.file_path,
            'scan_tool': getattr(result, 'scan_tool', None),
            'scan_duration': getattr(result, 'scan_duration', None),
            'scan_output': getattr(result, 'scan_output', None),
            'corruption_details': result.corruption_details
        })
    except Exception as e:
        logger.error(f"Error getting scan output: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/configurations')
def get_configurations():
    """Get all configuration settings"""
    try:
        configs = ScanConfiguration.query.all()
        return jsonify({
            'configurations': [config.to_dict() for config in configs]
        })
    except Exception as e:
        logger.error(f"Error getting configurations: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/configurations', methods=['POST'])
def update_configuration():
    """Update or create a configuration setting"""
    data = request.get_json()
    key = data.get('key')
    value = data.get('value')
    description = data.get('description', '')
    
    if not key or not value:
        return jsonify({'error': 'Key and value are required'}), 400
    
    try:
        existing = ScanConfiguration.query.filter_by(key=key).first()
        if existing:
            existing.value = value
            existing.description = description
            existing.updated_date = datetime.now(timezone.utc)
            config = existing
        else:
            config = ScanConfiguration(
                key=key,
                value=value,
                description=description
            )
            db.session.add(config)
        
        db.session.commit()
        
        logger.info(f"Updated configuration: {key} = {value}")
        return jsonify(config.to_dict())
    except Exception as e:
        logger.error(f"Error updating configuration: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# Add new migrations for the enhanced features
def migrate_database():
    """Handle database migrations"""
    try:
        # Check if scan_state table exists
        result = db.session.execute(db.text("SELECT name FROM sqlite_master WHERE type='table' AND name='scan_state'"))
        if not result.fetchone():
            logger.info("Creating scan_state table...")
            db.create_all()  # This will create any missing tables
            logger.info("scan_state table created successfully")
        
        # Check if scan_results table exists
        result = db.session.execute(db.text("SELECT name FROM sqlite_master WHERE type='table' AND name='scan_results'"))
        if not result.fetchone():
            logger.info("scan_results table does not exist, skipping migration")
            return
        
        # Get existing columns
        result = db.session.execute(db.text("PRAGMA table_info(scan_results)"))
        existing_columns = {row[1] for row in result.fetchall()}
        logger.info(f"Existing columns: {existing_columns}")
        
        # Define migrations including new fields
        migrations = [
            {
                'column': 'marked_as_good',
                'type': 'BOOLEAN DEFAULT 0',
                'post_update': None
            },
            {
                'column': 'scan_status',
                'type': 'VARCHAR(20) DEFAULT \'completed\'',
                'post_update': "UPDATE scan_results SET scan_status = 'completed' WHERE scan_status IS NULL"
            },
            {
                'column': 'discovered_date',
                'type': 'DATETIME',
                'post_update': "UPDATE scan_results SET discovered_date = scan_date WHERE discovered_date IS NULL"
            },
            {
                'column': 'file_hash',
                'type': 'VARCHAR(64)',
                'post_update': None
            },
            {
                'column': 'last_modified',
                'type': 'DATETIME',
                'post_update': None
            },
            {
                'column': 'scan_tool',
                'type': 'VARCHAR(50)',
                'post_update': None
            },
            {
                'column': 'scan_duration',
                'type': 'REAL',
                'post_update': None
            },
            {
                'column': 'scan_output',
                'type': 'TEXT',
                'post_update': None
            },
            {
                'column': 'ignored_error_types',
                'type': 'TEXT',
                'post_update': None
            },
            {
                'column': 'has_warnings',
                'type': 'BOOLEAN DEFAULT 0',
                'post_update': None
            },
            {
                'column': 'warning_details',
                'type': 'TEXT',
                'post_update': None
            },
            {
                'column': 'deep_scan',
                'type': 'BOOLEAN DEFAULT 0',
                'post_update': None
            }
        ]
        
        # Apply migrations
        for migration in migrations:
            if migration['column'] not in existing_columns:
                logger.info(f"Adding column: {migration['column']}")
                try:
                    db.session.execute(db.text(
                        f"ALTER TABLE scan_results ADD COLUMN {migration['column']} {migration['type']}"
                    ))
                    db.session.commit()
                    logger.info(f"Successfully added {migration['column']} column")
                    
                    # Run post-update if defined
                    if migration['post_update']:
                        db.session.execute(db.text(migration['post_update']))
                        db.session.commit()
                        logger.info(f"Updated data for {migration['column']}")
                        
                except Exception as e:
                    logger.error(f"Failed to add {migration['column']}: {str(e)}")
                    db.session.rollback()
        
        # Count existing records
        try:
            count = db.session.execute(db.text("SELECT COUNT(*) FROM scan_results")).scalar()
            logger.info(f"Database migration complete. Found {count} existing records.")
        except Exception as e:
            logger.error(f"Failed to count records: {str(e)}")
        
        # Migrate scan_schedules table
        try:
            # Check if scan_schedules table exists
            result = db.session.execute(db.text("SELECT name FROM sqlite_master WHERE type='table' AND name='scan_schedules'"))
            if result.fetchone():
                # Get existing columns for scan_schedules
                result = db.session.execute(db.text("PRAGMA table_info(scan_schedules)"))
                schedule_columns = {row[1] for row in result.fetchall()}
                
                # Add scan_type column if it doesn't exist
                if 'scan_type' not in schedule_columns:
                    logger.info("Adding scan_type column to scan_schedules table")
                    db.session.execute(db.text(
                        "ALTER TABLE scan_schedules ADD COLUMN scan_type VARCHAR(20) DEFAULT 'normal'"
                    ))
                    db.session.commit()
                    logger.info("Successfully added scan_type column")
            else:
                logger.info("scan_schedules table doesn't exist, will be created with scan_type column")
        except Exception as e:
            logger.error(f"Failed to migrate scan_schedules table: {str(e)}")
            db.session.rollback()
            
    except Exception as e:
        logger.error(f"Migration error: {str(e)}")
        db.session.rollback()

# Schedule Management API endpoints
@app.route('/api/schedules', methods=['GET'])
def get_schedules():
    """Get all scan schedules"""
    try:
        status = scheduler.get_schedule_status()
        return jsonify(status)
    except Exception as e:
        logger.error(f"Failed to get schedules: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/schedules', methods=['POST'])
def create_schedule():
    """Create a new scan schedule"""
    try:
        data = request.get_json()
        name = data.get('name')
        cron_expression = data.get('cron_expression')
        scan_paths = data.get('scan_paths', [])
        scan_type = data.get('scan_type', 'normal')  # normal, orphan, file_changes
        
        if not name or not cron_expression:
            return jsonify({'error': 'Name and cron_expression are required'}), 400
        
        # Validate scan_type
        if scan_type not in ['normal', 'orphan', 'file_changes']:
            return jsonify({'error': 'Invalid scan_type. Must be: normal, orphan, or file_changes'}), 400
            
        schedule = scheduler.create_schedule(name, cron_expression, scan_paths, scan_type)
        return jsonify(schedule.to_dict())
    except Exception as e:
        logger.error(f"Failed to create schedule: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/schedules/<int:schedule_id>', methods=['PUT'])
def update_schedule(schedule_id):
    """Update an existing schedule"""
    try:
        data = request.get_json()
        schedule = scheduler.update_schedule(schedule_id, **data)
        return jsonify(schedule.to_dict())
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.error(f"Failed to update schedule: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/schedules/<int:schedule_id>', methods=['DELETE'])
def delete_schedule(schedule_id):
    """Delete a schedule"""
    try:
        scheduler.delete_schedule(schedule_id)
        return jsonify({'message': 'Schedule deleted successfully'})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.error(f"Failed to delete schedule: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/exclusions', methods=['GET'])
def get_exclusions():
    """Get current path and extension exclusions"""
    return jsonify({
        'excluded_paths': scheduler.excluded_paths,
        'excluded_extensions': scheduler.excluded_extensions
    })

@app.route('/api/exclusions', methods=['PUT'])
def update_exclusions():
    """Update path and extension exclusions"""
    try:
        data = request.get_json()
        paths = data.get('excluded_paths')
        extensions = data.get('excluded_extensions')
        
        scheduler.update_exclusions(paths=paths, extensions=extensions)
        
        return jsonify({
            'excluded_paths': scheduler.excluded_paths,
            'excluded_extensions': scheduler.excluded_extensions
        })
    except Exception as e:
        logger.error(f"Failed to update exclusions: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/exclusions/<exclusion_type>', methods=['POST'])
def add_exclusion(exclusion_type):
    """Add a single exclusion"""
    try:
        if exclusion_type not in ['path', 'extension']:
            return jsonify({'error': 'Invalid exclusion type'}), 400
        
        data = request.get_json()
        if not data or 'item' not in data:
            return jsonify({'error': 'Item is required'}), 400
        
        item = data['item']
        
        if exclusion_type == 'path':
            if item not in scheduler.excluded_paths:
                scheduler.excluded_paths.append(item)
                scheduler.update_exclusions(paths=scheduler.excluded_paths)
        else:  # extension
            if item not in scheduler.excluded_extensions:
                scheduler.excluded_extensions.append(item)
                scheduler.update_exclusions(extensions=scheduler.excluded_extensions)
        
        return jsonify({
            'excluded_paths': scheduler.excluded_paths,
            'excluded_extensions': scheduler.excluded_extensions
        })
    except Exception as e:
        logger.error(f"Failed to add exclusion: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/exclusions/<exclusion_type>', methods=['DELETE'])
def remove_exclusion(exclusion_type):
    """Remove a single exclusion"""
    try:
        if exclusion_type not in ['path', 'extension']:
            return jsonify({'error': 'Invalid exclusion type'}), 400
        
        data = request.get_json()
        if not data or 'item' not in data:
            return jsonify({'error': 'Item is required'}), 400
        
        item = data['item']
        
        if exclusion_type == 'path':
            if item in scheduler.excluded_paths:
                scheduler.excluded_paths.remove(item)
                scheduler.update_exclusions(paths=scheduler.excluded_paths)
        else:  # extension
            if item in scheduler.excluded_extensions:
                scheduler.excluded_extensions.remove(item)
                scheduler.update_exclusions(extensions=scheduler.excluded_extensions)
        
        return jsonify({
            'excluded_paths': scheduler.excluded_paths,
            'excluded_extensions': scheduler.excluded_extensions
        })
    except Exception as e:
        logger.error(f"Failed to remove exclusion: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Initialize database when module is imported
create_tables()

# Start stats update thread on application startup
with app.app_context():
    # Initialize scheduler after app context is ready
    scheduler.init_app(app)
    
    # Clean up any stale active scans on startup
    try:
        stale_scans = ScanState.query.filter_by(is_active=True).all()
        if stale_scans:
            logger.info(f"Found {len(stale_scans)} stale active scans, marking as inactive")
            for scan in stale_scans:
                scan.is_active = False
                scan.phase = 'interrupted'
                scan.end_time = datetime.now(timezone.utc)
            db.session.commit()
    except Exception as e:
        logger.error(f"Error cleaning up stale scans: {str(e)}")
    
    # Start the stats update thread
    start_stats_update_thread()
    
    # Start the database write queue thread
    start_db_write_thread()

if __name__ == '__main__':
    logger.info("Starting Flask development server")
    app.run(debug=True, host='0.0.0.0', port=5001)
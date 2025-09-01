"""
PixelProbe Application - Refactored Version
This is a demonstration of how app.py would look with the new modular architecture
"""

import os
import logging
from datetime import datetime, timezone
from flask import Flask, jsonify, send_file, render_template, request
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
from pathlib import Path

# Import database and models
from models import db
from version import __version__, __github_url__
from scheduler import MediaScheduler

# Import blueprints from new modular structure
from pixelprobe.api.scan_routes import scan_bp
from pixelprobe.api.stats_routes import stats_bp
from pixelprobe.api.admin_routes import admin_bp, set_scheduler
from pixelprobe.api.export_routes import export_bp
from pixelprobe.api.maintenance_routes import maintenance_bp
from pixelprobe.api.reports_routes import reports_bp
from pixelprobe.api.scan_routes_parallel import parallel_scan_bp

# Import OpenAPI/Swagger documentation
try:
    from pixelprobe.api.swagger import api_bp as swagger_bp
    SWAGGER_AVAILABLE = True
except ImportError:
    SWAGGER_AVAILABLE = False
    logger.warning("flask-restx not installed, Swagger documentation unavailable")

# Import services
from pixelprobe.services import ScanService, StatsService, ExportService, MaintenanceService

# Import repositories
from pixelprobe.repositories import ScanRepository, ConfigurationRepository

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Timezone handling is now done via pixelprobe.utils.timezone module
APP_TIMEZONE = os.environ.get('TZ', 'UTC')
logger.info(f"Using timezone: {APP_TIMEZONE}")

# Create Flask app
app = Flask(__name__)

# Configure app
# Require SECRET_KEY in production - no insecure fallback
# Load configuration from config module
from config import get_config
config_name = os.getenv('FLASK_ENV', 'development')
config_class = get_config(config_name)
config_class.init_app(app)

# Backward compatibility - keep old environment variable support
if not app.config.get('SECRET_KEY'):
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        logger.error("SECRET_KEY environment variable is required for security")
        raise ValueError("SECRET_KEY environment variable must be set")
    app.config['SECRET_KEY'] = SECRET_KEY

# Legacy DATABASE_URL support for backward compatibility (will be removed in v2.3.0)
if os.environ.get('DATABASE_URL'):
    logger.warning("DATABASE_URL is deprecated since v2.2.0. Use POSTGRES_HOST, POSTGRES_USER, etc. instead.")
    logger.warning("DATABASE_URL support will be removed in v2.3.0.")
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')

# Initialize extensions
db.init_app(app)

# P1 Implementation: Initialize Celery task queue
from celery_config import create_celery, init_celery
celery = create_celery(app)
init_celery(app, celery)
# CRITICAL: Attach celery to app so scan_routes can find it
app.celery = celery

# PostgreSQL is the only supported database (v2.2.0+)

CORS(app, resources={
    r"/api/*": {"origins": "*"},
    r"/": {"origins": "*"}
})

# Custom key function that exempts internal requests
def get_rate_limit_key():
    """Get rate limit key, exempting internal requests"""
    remote_addr = get_remote_address()
    # Exempt localhost and common Docker internal IPs
    if remote_addr in ['127.0.0.1', 'localhost', '::1']:
        return None  # Returning None exempts from rate limiting
    # Exempt Docker internal networks (172.16.0.0/12, 10.0.0.0/8, 192.168.0.0/16)
    if (remote_addr.startswith('172.') or 
        remote_addr.startswith('10.') or 
        remote_addr.startswith('192.168.')):
        return None  # Returning None exempts from rate limiting
    return remote_addr

# Initialize rate limiter with proper configuration
limiter = Limiter(
    app=app,
    key_func=get_rate_limit_key,
    default_limits=[],  # Remove default limits to prevent spam when key_func returns None
    storage_uri="memory://",
    headers_enabled=True,
    swallow_errors=True  # Don't fail requests if rate limiting has issues
)

# Initialize CSRF protection
csrf = CSRFProtect(app)
# Exempt API endpoints from CSRF for now (will need to implement token-based auth)
csrf.exempt(scan_bp)
csrf.exempt(stats_bp)
csrf.exempt(admin_bp)
csrf.exempt(export_bp)
csrf.exempt(maintenance_bp)
csrf.exempt(reports_bp)

# Initialize scheduler
scheduler = MediaScheduler()

# Initialize services (would be done with dependency injection in production)
app.scan_service = None
app.stats_service = None
app.export_service = None
app.maintenance_service = None

# Initialize repositories
app.scan_repository = None
app.config_repository = None

def init_services():
    """Initialize services with app context"""
    app.scan_service = ScanService(app.config['SQLALCHEMY_DATABASE_URI'])
    app.stats_service = StatsService()
    app.export_service = ExportService()
    app.maintenance_service = MaintenanceService(app.config['SQLALCHEMY_DATABASE_URI'])
    
    app.scan_repository = ScanRepository()
    app.config_repository = ConfigurationRepository()

# Register blueprints
app.register_blueprint(scan_bp)
app.register_blueprint(stats_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(export_bp)
app.register_blueprint(maintenance_bp)
app.register_blueprint(reports_bp)
app.register_blueprint(parallel_scan_bp)

# Register Swagger blueprint if available
if SWAGGER_AVAILABLE:
    app.register_blueprint(swagger_bp)
    # Exempt swagger blueprint from CSRF
    csrf.exempt(swagger_bp)
    # Import swagger routes after blueprint registration to avoid circular imports
    import pixelprobe.api.swagger_routes
    logger.info("Swagger API documentation available at /api/v1/docs")

# Rate limiting exemptions are handled by the key_func returning None for internal IPs

# Rate limits are now applied directly on the route functions using decorators

# Pass scheduler to admin blueprint
set_scheduler(scheduler)

# Basic routes that remain in app.py
@app.route('/')
def index():
    """Serve the main application page"""
    return render_template('index.html', version=__version__, github_url=__github_url__)

@app.route('/api-docs')
def api_docs():
    """Redirect to Swagger UI documentation"""
    if SWAGGER_AVAILABLE:
        from flask import redirect
        return redirect('/api/v1/docs')
    else:
        # Fallback to old documentation if Swagger not available
        return render_template('api_docs.html', version=__version__)

@app.route('/health')
@limiter.exempt
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'version': __version__,
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

@app.route('/api/version')
def get_version():
    """Get application version"""
    logger.info("Version information requested")
    return jsonify({
        'version': __version__,
        'github_url': __github_url__,
        'api_version': '1.0'
    })

# Static file routes
@app.route('/favicon.ico')
def favicon():
    """Serve favicon"""
    return send_file('static/images/favicon-32x32.png', mimetype='image/png')

@app.route('/static/images/pixelprobe-logo.png')
def logo():
    """Serve logo"""
    logo_path = os.path.join(app.root_path, 'static', 'images', 'pixelprobe-logo.png')
    if os.path.exists(logo_path):
        return send_file(logo_path, mimetype='image/png')
    return '', 404

def cleanup_stuck_operations():
    """Clean up any stuck operations from previous runs"""
    try:
        from models import FileChangesState, CleanupState
        
        # Mark any active file changes as failed
        active_file_changes = FileChangesState.query.filter_by(is_active=True).all()
        for file_change in active_file_changes:
            file_change.is_active = False
            file_change.phase = 'failed'
            file_change.end_time = datetime.now(timezone.utc)
            file_change.progress_message = 'Application restarted - operation marked as failed'
            logger.warning(f"Marking stuck file changes operation {file_change.check_id} as failed")
        
        # Mark any active cleanup operations as failed
        active_cleanups = CleanupState.query.filter_by(is_active=True).all()
        for cleanup in active_cleanups:
            cleanup.is_active = False
            cleanup.phase = 'failed'
            cleanup.end_time = datetime.now(timezone.utc)
            cleanup.progress_message = 'Application restarted - operation marked as failed'
            logger.warning(f"Marking stuck cleanup operation {cleanup.cleanup_id} as failed")
        
        if active_file_changes or active_cleanups:
            db.session.commit()
            logger.info(f"Cleaned up {len(active_file_changes)} stuck file changes and {len(active_cleanups)} stuck cleanup operations")
            
    except Exception as e:
        logger.error(f"Error cleaning up stuck operations: {str(e)}")

def create_tables():
    """Initialize database tables and run migrations"""
    with app.app_context():
        try:
            # Use inspector to check existing tables
            from sqlalchemy import inspect, exc, text
            
            try:
                inspector = inspect(db.engine)
                existing_tables = inspector.get_table_names()
                
                # Only create tables that don't exist
                for table_name, table in db.metadata.tables.items():
                    if table_name not in existing_tables:
                        try:
                            table.create(db.engine)
                            logger.info(f"Created table: {table_name}")
                        except exc.OperationalError as e:
                            # Table might have been created by another worker
                            if "already exists" not in str(e):
                                logger.error(f"Error creating table {table_name}: {str(e)}")
                
                logger.info("Database tables verified successfully")
                
                # Run migrations for v2.2.68 - add tracking columns if they don't exist
                if 'scan_state' in existing_tables:
                    try:
                        # Check if new columns exist
                        columns = [col['name'] for col in inspector.get_columns('scan_state')]
                        
                        # Add missing columns with safe migration
                        with db.engine.connect() as conn:
                            if 'num_workers' not in columns:
                                try:
                                    conn.execute(text("ALTER TABLE scan_state ADD COLUMN num_workers INTEGER DEFAULT 1"))
                                    conn.commit()
                                    logger.info("Added num_workers column to scan_state table")
                                except exc.OperationalError as e:
                                    if "already exists" not in str(e).lower():
                                        logger.warning(f"Could not add num_workers column: {e}")
                            
                            if 'files_added' not in columns:
                                try:
                                    conn.execute(text("ALTER TABLE scan_state ADD COLUMN files_added INTEGER DEFAULT 0"))
                                    conn.commit()
                                    logger.info("Added files_added column to scan_state table")
                                except exc.OperationalError as e:
                                    if "already exists" not in str(e).lower():
                                        logger.warning(f"Could not add files_added column: {e}")
                            
                            if 'files_updated' not in columns:
                                try:
                                    conn.execute(text("ALTER TABLE scan_state ADD COLUMN files_updated INTEGER DEFAULT 0"))
                                    conn.commit()
                                    logger.info("Added files_updated column to scan_state table")
                                except exc.OperationalError as e:
                                    if "already exists" not in str(e).lower():
                                        logger.warning(f"Could not add files_updated column: {e}")
                                        
                    except Exception as e:
                        logger.warning(f"Migration check failed (non-critical): {e}")
                
            except exc.OperationalError as e:
                # This might happen if the database is locked or another worker created tables
                if "already exists" not in str(e):
                    logger.error(f"Database operation error: {str(e)}")
                else:
                    logger.info("Tables already exist (created by another worker)")
                    
            migrate_database()
            cleanup_stuck_operations()
            
        except Exception as e:
            logger.error(f"Error in database initialization: {str(e)}")
            # Don't stop the application for table creation errors
            # The tables might already exist and be functional

def migrate_database():
    """Run database migrations"""
    from tools.app_startup_migration import run_startup_migrations
    
    try:
        # Run startup migrations for v2.0.89
        run_startup_migrations(db)
        
        # Run v2.2.62 migrations - add missing columns
        run_v2_2_62_migrations()
        
        # Run v2.2.89 migrations - fix deep_scan column
        run_v2_2_89_migrations()
        
        # Create performance indexes
        create_performance_indexes()
        
        # All old column migrations removed - PostgreSQL schema should be up to date
        logger.info("Database initialization completed")
        
    except Exception as e:
        logger.error(f"Error during database initialization: {e}")

def run_v2_2_89_migrations():
    """Run migrations for v2.2.89 - fix deep_scan column constraint"""
    from sqlalchemy import text
    
    try:
        with db.engine.connect() as conn:
            # Check if deep_scan column exists and has NOT NULL constraint
            result = conn.execute(text("""
                SELECT 
                    column_name,
                    is_nullable,
                    column_default
                FROM information_schema.columns 
                WHERE table_name = 'scan_results' 
                AND column_name = 'deep_scan'
            """))
            
            row = result.fetchone()
            if row:
                column_name, is_nullable, column_default = row
                
                # If column exists and has NOT NULL constraint, fix it
                if is_nullable == 'NO':
                    logger.info("Applying migration: Fixing deep_scan column NOT NULL constraint")
                    
                    # Make column nullable and add default
                    conn.execute(text("""
                        ALTER TABLE scan_results 
                        ALTER COLUMN deep_scan DROP NOT NULL
                    """))
                    
                    conn.execute(text("""
                        ALTER TABLE scan_results 
                        ALTER COLUMN deep_scan SET DEFAULT FALSE
                    """))
                    
                    # Update any NULL values to FALSE
                    conn.execute(text("""
                        UPDATE scan_results 
                        SET deep_scan = FALSE 
                        WHERE deep_scan IS NULL
                    """))
                    
                    conn.commit()
                    logger.info("Migration completed: deep_scan column is now nullable with default FALSE")
                else:
                    logger.debug("Migration already applied: deep_scan column is already nullable")
            else:
                logger.debug("No deep_scan column found - this is expected for new installations")
                
    except Exception as e:
        logger.error(f"Migration v2.2.89 failed: {e}")
        # Don't fail startup - the temporary model fix will handle it

def run_v2_2_62_migrations():
    """Run migrations for v2.2.62 - add celery_task_id column"""
    from sqlalchemy import text
    
    try:
        with db.engine.connect() as conn:
            # Check if celery_task_id column exists in scan_chunks
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'scan_chunks' 
                AND column_name = 'celery_task_id'
            """))
            
            if not result.fetchone():
                logger.info("Applying migration: Adding celery_task_id column to scan_chunks table")
                conn.execute(text("""
                    ALTER TABLE scan_chunks 
                    ADD COLUMN celery_task_id VARCHAR(36)
                """))
                
                # Create index for performance
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_scan_chunks_celery_task_id 
                    ON scan_chunks (celery_task_id) 
                    WHERE celery_task_id IS NOT NULL
                """))
                
                conn.commit()
                logger.info("Migration completed: celery_task_id column added successfully")
            else:
                logger.debug("Migration already applied: celery_task_id column exists")
                
    except Exception as e:
        logger.error(f"Migration v2.2.62 failed: {e}")
        # Don't fail startup - app might still work without this column

def create_performance_indexes():
    """Create performance indexes"""
    from sqlalchemy import text
    
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
        "CREATE INDEX IF NOT EXISTS idx_corrupted_good ON scan_results(is_corrupted, marked_as_good)",
        "CREATE INDEX IF NOT EXISTS idx_file_path_status ON scan_results(file_path, scan_status)"
    ]
    
    logger.info("Creating performance indexes...")
    with db.engine.connect() as conn:
        for index in indexes:
            try:
                conn.execute(text(index))
            except Exception as e:
                logger.warning(f"Could not create index: {e}")
        conn.commit()
    
    logger.info("Performance indexes created successfully")

# Initialize on startup for better Docker compatibility
with app.app_context():
    # CRITICAL: create_tables() MUST run first to ensure migration happens
    # before any model queries. This runs migrate_database() which adds
    # the celery_task_id column that the ScanState model expects.
    create_tables()
    init_services()
    scheduler.init_app(app)
    
    # Clean up any stuck scans from previous runs (7+ days old)
    # NOTE: This query happens AFTER migration, so celery_task_id column exists
    try:
        from datetime import datetime, timezone, timedelta
        from models import ScanState
        stuck_scans = ScanState.query.filter(
            ScanState.is_active == True,
            ScanState.start_time < datetime.now(timezone.utc) - timedelta(days=7)
        ).all()
        
        for scan in stuck_scans:
            logger.warning(f"Found very old scan {scan.id} from {scan.start_time}, marking as errored")
            scan.error_scan("Scan was abandoned from previous application run")
        
        if stuck_scans:
            db.session.commit()
            logger.info(f"Cleaned up {len(stuck_scans)} abandoned scans")
    except Exception as e:
        # If the query fails (e.g., column doesn't exist yet), log but don't crash
        logger.warning(f"Could not clean up stuck scans on startup: {e}")
        # This is not critical for app startup, so we continue

if __name__ == '__main__':
    # Start the application (initialization already done above)
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        debug=os.environ.get('DEBUG', 'False').lower() == 'true'
    )
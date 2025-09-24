"""
PixelProbe Application - Refactored Version
This is a demonstration of how app.py would look with the new modular architecture
"""

import os
import logging
from datetime import datetime, timezone
from flask import Flask, jsonify, send_file, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_login import login_required, current_user
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
from pixelprobe.api.auth_routes import auth_bp

# Import authentication module
from auth import init_auth, auth_required

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

# Initialize authentication
init_auth(app)

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
csrf.exempt(auth_bp)  # Exempt auth endpoints from CSRF

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
app.register_blueprint(auth_bp)  # Register auth blueprint first

# Import auth decorator wrapper
from pixelprobe.api.auth_decorator import apply_auth_to_blueprint

# Register and protect API blueprints
app.register_blueprint(scan_bp)
apply_auth_to_blueprint(scan_bp)

app.register_blueprint(stats_bp)
apply_auth_to_blueprint(stats_bp)

app.register_blueprint(admin_bp)
apply_auth_to_blueprint(admin_bp)

app.register_blueprint(export_bp)
apply_auth_to_blueprint(export_bp)

app.register_blueprint(maintenance_bp)
apply_auth_to_blueprint(maintenance_bp)

app.register_blueprint(reports_bp)
apply_auth_to_blueprint(reports_bp)

app.register_blueprint(parallel_scan_bp)
apply_auth_to_blueprint(parallel_scan_bp)

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
@login_required
def index():
    """Serve the main application page"""
    return render_template('index.html', version=__version__, github_url=__github_url__, user=current_user)

@app.route('/api-docs')
@login_required
def api_docs():
    """Redirect to Swagger UI documentation"""
    if SWAGGER_AVAILABLE:
        return redirect('/api/v1/docs')
    else:
        # Fallback to old documentation if Swagger not available
        return render_template('api_docs.html', version=__version__)

@app.route('/health')
@limiter.exempt
@auth_required
def health_check():
    """Health check endpoint - requires authentication"""
    return jsonify({
        'status': 'healthy',
        'version': __version__,
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

@app.route('/api/version')
@auth_required
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
    # Run startup migrations
    logger.info("Running startup migrations...")
    from tools.app_startup_migration import run_startup_migrations
    try:
        run_startup_migrations(db)
        logger.info("Startup migrations completed successfully")
    except Exception as e:
        logger.error(f"Startup migration failed: {e}")

    # Run authentication tables migration for v2.4.0
    logger.info("Checking authentication tables...")
    try:
        run_auth_migration()
        logger.info("Authentication tables verified")
    except Exception as e:
        logger.error(f"Authentication migration failed: {e}")

    # Test v2.2.62 migration
    logger.info("Running v2.2.62 migration...")
    try:
        run_v2_2_62_migrations()
        logger.info("v2.2.62 migration completed successfully")
    except Exception as e:
        logger.error(f"v2.2.62 migration failed: {e}")
    
    # Skip v2.2.90 migration - it was causing startup hang
    # run_v2_2_90_migrations()  # DISABLED - causing startup hang
    
    # Create performance indexes
    logger.info("Creating performance indexes...")
    try:
        create_performance_indexes()
        logger.info("Performance indexes created successfully")
    except Exception as e:
        logger.error(f"Failed to create performance indexes: {e}")
    
    logger.info("Database initialization completed")

def run_auth_migration():
    """Run authentication tables migration for v2.4.0"""
    from sqlalchemy import text, inspect

    try:
        # Check if tables already exist
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()

        with db.engine.connect() as conn:
            # Create users table if it doesn't exist
            if 'users' not in existing_tables:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        username VARCHAR(80) UNIQUE NOT NULL,
                        email VARCHAR(120) UNIQUE NOT NULL,
                        password_hash VARCHAR(128) NOT NULL,
                        is_admin BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_login TIMESTAMP WITH TIME ZONE,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        first_setup_required BOOLEAN NOT NULL DEFAULT FALSE
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"))
                logger.info("Created users table via migration")

            # Create API tokens table if it doesn't exist
            if 'api_tokens' not in existing_tables:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS api_tokens (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        token VARCHAR(64) UNIQUE NOT NULL,
                        description VARCHAR(200),
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_used TIMESTAMP WITH TIME ZONE,
                        expires_at TIMESTAMP WITH TIME ZONE,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_api_tokens_token ON api_tokens(token)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_api_tokens_user_id ON api_tokens(user_id)"))
                logger.info("Created api_tokens table via migration")

            # Check if any users exist (only if table exists)
            if 'users' in existing_tables or 'users' in inspector.get_table_names():
                result = conn.execute(text("SELECT COUNT(*) FROM users"))
                user_count = result.scalar()
            else:
                user_count = 0

            if user_count == 0 and 'users' in inspector.get_table_names():
                # Create a default admin user that requires setup on first login
                conn.execute(text("""
                    INSERT INTO users (username, email, password_hash, is_admin, first_setup_required, created_at, is_active)
                    VALUES ('admin', 'admin@pixelprobe.local', '', TRUE, TRUE, CURRENT_TIMESTAMP, TRUE)
                """))
                logger.info("Created default admin user (password setup required on first login)")

            conn.commit()

    except Exception as e:
        # Don't fail startup if migration issues
        logger.warning(f"Authentication migration encountered issues: {e}")

def run_v2_2_90_migrations():
    """Run migrations for v2.2.90 - fix deep_scan column constraint"""
    from sqlalchemy import text
    import os
    
    # Only run migration on first worker to avoid conflicts
    worker_id = os.environ.get('SERVER_SOFTWARE', '').split('/')[-1] if 'SERVER_SOFTWARE' in os.environ else '0'
    if worker_id not in ['0', '7', '1', '']:  # Only first worker or main process
        logger.debug(f"Skipping v2.2.90 migration on worker {worker_id}")
        return
    
    try:
        with db.engine.connect() as conn:
            # Check if deep_scan column exists
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'scan_results' 
                AND column_name = 'deep_scan'
                LIMIT 1
            """))
            
            if result.fetchone():
                logger.info("Applying migration: Removing deep_scan column constraint")
                
                # Use separate transactions for DDL commands
                try:
                    # First transaction: Drop NOT NULL constraint
                    with db.engine.begin() as txn:
                        txn.execute(text("""
                            ALTER TABLE scan_results 
                            ALTER COLUMN deep_scan DROP NOT NULL
                        """))
                    logger.debug("Dropped NOT NULL constraint on deep_scan")
                except Exception as e:
                    if 'already nullable' not in str(e).lower() and 'not null' not in str(e).lower():
                        logger.debug(f"Could not drop NOT NULL: {e}")
                
                try:
                    # Second transaction: Set default
                    with db.engine.begin() as txn:
                        txn.execute(text("""
                            ALTER TABLE scan_results 
                            ALTER COLUMN deep_scan SET DEFAULT FALSE
                        """))
                    logger.debug("Set default FALSE on deep_scan")
                except Exception as e:
                    logger.debug(f"Could not set default: {e}")
                
                try:
                    # Third transaction: Update NULL values
                    with db.engine.begin() as txn:
                        result = txn.execute(text("""
                            UPDATE scan_results 
                            SET deep_scan = FALSE 
                            WHERE deep_scan IS NULL
                        """))
                        if result.rowcount > 0:
                            logger.info(f"Updated {result.rowcount} NULL deep_scan values to FALSE")
                except Exception as e:
                    logger.debug(f"Could not update NULL values: {e}")
                
                logger.info("Migration v2.2.90 completed")
            else:
                logger.debug("No deep_scan column found - expected for new installations")
                
    except Exception as e:
        logger.warning(f"Migration v2.2.90 encountered issues: {e}")
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
    created_count = 0
    for index_sql in indexes:
        try:
            # Use separate transaction for each index
            with db.engine.begin() as conn:
                conn.execute(text(index_sql))
            created_count += 1
        except Exception as e:
            # Index might already exist or column might not exist
            if 'already exists' not in str(e).lower() and 'does not exist' not in str(e).lower():
                logger.debug(f"Could not create index: {e}")
    
    if created_count > 0:
        logger.info(f"Created {created_count} performance indexes")
    else:
        logger.debug("All performance indexes already exist")

# Initialize on startup for better Docker compatibility
with app.app_context():
    # CRITICAL: create_tables() MUST run first to ensure migration happens
    # before any model queries. This runs migrate_database() which adds
    # the celery_task_id column that the ScanState model expects.
    create_tables()
    init_services()
    

    # Use a file lock to ensure only one scheduler runs across all workers
    import fcntl
    scheduler_lock_file = '/tmp/pixelprobe_scheduler.lock'
    
    try:
        # Try to acquire exclusive lock (non-blocking)
        lock_file = open(scheduler_lock_file, 'w')
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        
        # We got the lock, so we're the scheduler process
        logger.info(f"Acquired scheduler lock in process {os.getpid()}, initializing scheduler")
        scheduler.init_app(app)
        
        # Keep the lock file open to maintain the lock
        app.scheduler_lock_file = lock_file
        
    except (IOError, OSError) as e:
        # Another process has the lock
        logger.info(f"Scheduler already running in another process, skipping initialization in process {os.getpid()}")
    

    # Clean up ALL active scans from previous runs - they can't still be running after restart
    # NOTE: This query happens AFTER migration, so celery_task_id column exists
    try:
        from datetime import datetime, timezone, timedelta
        from models import ScanState
        stuck_scans = ScanState.query.filter(
            ScanState.is_active == True
        ).all()
        
        for scan in stuck_scans:
            logger.warning(f"Found active scan {scan.id} from {scan.start_time}, marking as crashed (app restarted)")
            scan.is_active = False
            scan.phase = 'crashed'
            scan.error_message = "Application restarted - scan was interrupted"
        
        if stuck_scans:
            db.session.commit()
            logger.info(f"Cleaned up {len(stuck_scans)} abandoned scans from previous run")
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
"""
PixelProbe Application - Refactored Version
This is a demonstration of how app.py would look with the new modular architecture
"""

import os
import sys
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
import json

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
from pixelprobe.api.healthcheck_routes import healthcheck_bp
from pixelprobe.api.notification_routes import notification_bp  # P3 audit: Notification API
from pixelprobe.api.auth_routes import auth_api_bp, auth_ui_bp, auth_bp  # auth_bp for backward compat

# Import authentication module
from auth import init_auth, auth_required

# OpenAPI documentation is available as openapi.yaml in the project root

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

# Security headers middleware (P1 audit fix)
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    # Prevent clickjacking
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'

    # Prevent MIME type sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'

    # XSS Protection (legacy browsers)
    response.headers['X-XSS-Protection'] = '1; mode=block'

    # Referrer Policy
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

    # Content Security Policy (unsafe-inline required - inline handlers kept per user decision)
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' https://cdn.jsdelivr.net; "
        "frame-ancestors 'self';"
    )
    response.headers['Content-Security-Policy'] = csp

    # HSTS (only if HTTPS)
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    return response

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
csrf.exempt(parallel_scan_bp)  # Added: parallel scan endpoint was missing CSRF exemption
csrf.exempt(stats_bp)
csrf.exempt(admin_bp)
csrf.exempt(export_bp)
csrf.exempt(maintenance_bp)
csrf.exempt(reports_bp)
csrf.exempt(auth_api_bp)  # Exempt API auth endpoints from CSRF
csrf.exempt(healthcheck_bp)  # Exempt healthcheck API endpoints from CSRF
csrf.exempt(notification_bp)  # Exempt notification API endpoints from CSRF (P3 audit)
# Note: auth_ui_bp (login/logout pages) should NOT be exempted from CSRF

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


def sync_scan_paths_to_db():
    """
    Sync SCAN_PATHS from environment variable to database on startup.
    This allows the celery-worker to read paths from DB instead of needing
    the env var set in its container.

    Uses INSERT ... ON CONFLICT DO NOTHING to handle race conditions when
    multiple gunicorn workers start simultaneously.
    """
    from models import ScanConfiguration
    from sqlalchemy.dialects.postgresql import insert

    scan_paths = app.config.get('SCAN_PATHS', [])
    if not scan_paths:
        logger.info("No SCAN_PATHS configured in environment, skipping DB sync")
        return

    try:
        for path in scan_paths:
            if path:
                # Use PostgreSQL upsert to handle race conditions
                # Multiple workers may try to insert the same path simultaneously
                stmt = insert(ScanConfiguration).values(
                    path=path,
                    is_active=True,
                    created_at=datetime.now(timezone.utc)
                ).on_conflict_do_nothing(index_elements=['path'])
                db.session.execute(stmt)

        db.session.commit()

        # Log what's now in DB
        configs = ScanConfiguration.query.filter_by(is_active=True).all()
        paths = [c.path for c in configs if c.path]
        logger.info(f"SCAN_PATHS in database: {paths}")
    except Exception as e:
        logger.error(f"Error syncing SCAN_PATHS to database: {e}")
        db.session.rollback()

# Register blueprints
app.register_blueprint(auth_api_bp)  # Register API auth blueprint first
app.register_blueprint(auth_ui_bp)   # Register UI auth blueprint (login/logout pages)

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

app.register_blueprint(healthcheck_bp)
apply_auth_to_blueprint(healthcheck_bp)

# P3 audit: Register notification API routes
app.register_blueprint(notification_bp)
apply_auth_to_blueprint(notification_bp)

# API documentation is now provided via openapi.yaml specification file

# Rate limiting exemptions are handled by the key_func returning None for internal IPs

# Rate limits are now applied directly on the route functions using decorators

# Pass scheduler to admin blueprint
set_scheduler(scheduler)

# Webpack manifest loader for production builds
_webpack_manifest = None

def load_webpack_manifest():
    """Load webpack manifest for hashed asset filenames"""
    global _webpack_manifest
    if _webpack_manifest is None:
        manifest_path = Path(__file__).parent / 'static' / 'dist' / 'manifest.json'
        if manifest_path.exists():
            with open(manifest_path, 'r') as f:
                _webpack_manifest = json.load(f)
        else:
            _webpack_manifest = {}
    return _webpack_manifest

@app.context_processor
def inject_assets():
    """Inject asset helper function into templates"""
    manifest = load_webpack_manifest()

    def asset_url(filename):
        """Get the hashed filename from webpack manifest, fallback to original"""
        if manifest and filename in manifest:
            return manifest[filename]
        # Fallback for development or if manifest doesn't exist
        return f'/static/{filename}?v={__version__}'

    return dict(asset_url=asset_url, version=__version__, github_url=__github_url__)

# Basic routes that remain in app.py
@app.route('/')
@login_required
def index():
    """Serve the main application page"""
    return render_template('index.html', user=current_user)

@app.route('/api-docs')
@login_required
def api_docs():
    """Serve API documentation page"""
    return render_template('api_docs.html')

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
    """Get application version and infrastructure component versions"""
    logger.info("Version information requested")

    # Get infrastructure versions
    infrastructure = {}

    # Celery version
    try:
        import celery
        infrastructure['celery'] = celery.__version__
    except Exception as e:
        logger.warning(f"Could not get Celery version: {e}")
        infrastructure['celery'] = 'unknown'

    # Redis version - v2.5.51: Use robust Redis connection from progress_utils
    try:
        from pixelprobe.progress_utils import get_redis_info
        redis_info = get_redis_info('server')
        if redis_info:
            infrastructure['redis'] = redis_info.get('redis_version', 'unknown')
        else:
            infrastructure['redis'] = 'unavailable'
    except Exception as e:
        logger.warning(f"Could not get Redis version: {e}")
        infrastructure['redis'] = 'unavailable'

    # PostgreSQL version
    try:
        from sqlalchemy import text
        result = db.session.execute(text('SELECT version()')).fetchone()
        if result:
            # Extract just the version number from full string like "PostgreSQL 15.2 (Debian 15.2-1.pgdg110+1)..."
            pg_version_full = result[0]
            # Extract version number (e.g., "15.2")
            import re
            match = re.search(r'PostgreSQL (\d+\.\d+)', pg_version_full)
            infrastructure['postgresql'] = match.group(1) if match else pg_version_full
        else:
            infrastructure['postgresql'] = 'unknown'
    except Exception as e:
        logger.warning(f"Could not get PostgreSQL version: {e}")
        infrastructure['postgresql'] = 'unavailable'

    return jsonify({
        'version': __version__,
        'github_url': __github_url__,
        'api_version': '1.0',
        'infrastructure': infrastructure
    })

@app.route('/api/openapi.yaml')
@limiter.exempt
def get_openapi_yaml():
    """Serve OpenAPI specification in YAML format with dynamic version"""
    import yaml
    import os

    # Read the openapi.yaml file
    openapi_path = os.path.join(os.path.dirname(__file__), 'openapi.yaml')

    try:
        with open(openapi_path, 'r') as f:
            spec = yaml.safe_load(f)

        # Update version dynamically from version.py
        spec['info']['version'] = __version__

        # Convert back to YAML
        yaml_content = yaml.dump(spec, default_flow_style=False, sort_keys=False)

        from flask import Response
        return Response(yaml_content, mimetype='application/x-yaml')
    except Exception as e:
        logger.error(f"Error serving OpenAPI spec: {e}")
        return jsonify({'error': 'OpenAPI specification not available'}), 404

@app.route('/api/openapi.json')
@limiter.exempt
def get_openapi_json():
    """Serve OpenAPI specification in JSON format with dynamic version"""
    import yaml
    import os

    # Read the openapi.yaml file
    openapi_path = os.path.join(os.path.dirname(__file__), 'openapi.yaml')

    try:
        with open(openapi_path, 'r') as f:
            spec = yaml.safe_load(f)

        # Update version dynamically from version.py
        spec['info']['version'] = __version__

        return jsonify(spec)
    except Exception as e:
        logger.error(f"Error serving OpenAPI spec: {e}")
        return jsonify({'error': 'OpenAPI specification not available'}), 404

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
    logger.info(f"Starting PixelProbe v{__version__}")
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
                        except (exc.OperationalError, exc.IntegrityError, exc.ProgrammingError) as e:
                            # Table might have been created by another worker - suppress common race condition errors
                            err_str = str(e).lower()
                            if any(msg in err_str for msg in ["already exists", "duplicate key", "typname_nsp_index"]):
                                logger.debug(f"Table {table_name} already created by another worker")
                            else:
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

MIGRATION_ADVISORY_LOCK_ID = 7283945162

def _run_all_migrations():
    """Execute all database migrations. Called by migrate_database() after acquiring lock."""
    from tools.app_startup_migration import run_startup_migrations

    # Run startup migrations
    logger.info("Running startup migrations...")
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

    # Run v2.4.35 migration
    logger.info("Running v2.4.35 migration...")
    try:
        run_v2_4_35_migrations()
        logger.info("v2.4.35 migration completed successfully")
    except Exception as e:
        logger.error(f"v2.4.35 migration failed: {e}")

    # Run v2.4.113 migration
    logger.info("Running v2.4.113 migration...")
    try:
        run_v2_4_113_migrations()
        logger.info("v2.4.113 migration completed successfully")
    except Exception as e:
        logger.error(f"v2.4.113 migration failed: {e}")

    # Create performance indexes
    logger.info("Creating performance indexes...")
    try:
        create_performance_indexes()
        logger.info("Performance indexes created successfully")
    except Exception as e:
        logger.error(f"Failed to create performance indexes: {e}")

    logger.info("Database initialization completed")

def migrate_database():
    """Run database migrations - uses PostgreSQL advisory lock to coordinate across containers.

    Advisory locks work across all connections to the same database, unlike file locks
    which are scoped to a single container's filesystem. This prevents the
    'duplicate key value violates unique constraint pg_class_relname_nsp_index' errors
    that occurred when app and celery-worker containers raced during CREATE INDEX.
    """
    from sqlalchemy import text

    lock_conn = None
    try:
        # Get a dedicated connection for the advisory lock
        lock_conn = db.engine.connect()

        # Try non-blocking lock acquisition
        result = lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": MIGRATION_ADVISORY_LOCK_ID}
        )
        acquired = result.scalar()

        if acquired:
            # We are the migration leader
            logger.info(f"Acquired PostgreSQL advisory lock in process {os.getpid()}, running migrations")
            try:
                _run_all_migrations()
            except Exception as mig_err:
                logger.error(f"Migration error (lock held): {mig_err}")
            finally:
                lock_conn.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": MIGRATION_ADVISORY_LOCK_ID}
                )
                logger.info("Released PostgreSQL advisory lock")
        else:
            # Another process holds the lock - wait for it to finish
            logger.info(f"Migrations already running in another process, waiting for completion (process {os.getpid()})...")
            lock_conn.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": MIGRATION_ADVISORY_LOCK_ID}
            )
            # Lock acquired means the leader finished; release immediately
            lock_conn.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": MIGRATION_ADVISORY_LOCK_ID}
            )
            logger.info(f"Migrations completed by another process, continuing startup in process {os.getpid()}")

    except Exception as e:
        # Advisory lock failed (e.g., connection error, non-PostgreSQL database)
        # Fall back to running migrations uncoordinated - each DDL statement
        # already has its own idempotency handling (IF NOT EXISTS, try/except)
        logger.warning(f"Could not use advisory lock ({e}), running migrations without coordination")
        _run_all_migrations()

    finally:
        if lock_conn is not None:
            try:
                lock_conn.close()
            except Exception:
                pass

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

            # No longer create default admin user automatically
            # Users must use the /api/auth/setup endpoint on first run
            logger.info("Authentication tables migration completed")

            conn.commit()

    except Exception as e:
        # Don't fail startup if migration issues
        logger.warning(f"Authentication migration encountered issues: {e}")

def run_v2_4_35_migrations():
    """Run migrations for v2.4.35 - add last_heartbeat column to file_changes_state"""
    from sqlalchemy import text

    try:
        with db.engine.connect() as conn:
            # Check if file_changes_state table exists first
            table_check = conn.execute(text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_name = 'file_changes_state'
            """))

            if not table_check.fetchone():
                logger.debug("file_changes_state table does not exist - skipping migration (new installation)")
                return

            # Check if last_heartbeat column exists in file_changes_state
            result = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'file_changes_state'
                AND column_name = 'last_heartbeat'
            """))

            if not result.fetchone():
                logger.info("Applying migration: Adding last_heartbeat column to file_changes_state table")
                conn.execute(text("""
                    ALTER TABLE file_changes_state
                    ADD COLUMN last_heartbeat TIMESTAMP WITH TIME ZONE
                """))

                conn.commit()
                logger.info("Migration completed: last_heartbeat column added successfully")
            else:
                logger.debug("Migration already applied: last_heartbeat column exists")

    except Exception as e:
        logger.error(f"Migration v2.4.35 failed: {e}")
        # Don't fail startup - app might still work without this column

def run_v2_4_113_migrations():
    """Run migrations for v2.4.113 - add last_integrity_check_date column to scan_results"""
    from sqlalchemy import text

    try:
        with db.engine.connect() as conn:
            # Check if scan_results table exists first
            table_check = conn.execute(text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_name = 'scan_results'
            """))

            if not table_check.fetchone():
                logger.debug("scan_results table does not exist - skipping migration (new installation)")
                return

            # Check if last_integrity_check_date column exists in scan_results
            result = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'scan_results'
                AND column_name = 'last_integrity_check_date'
            """))

            if not result.fetchone():
                logger.info("Applying migration: Adding last_integrity_check_date column to scan_results table")
                conn.execute(text("""
                    ALTER TABLE scan_results
                    ADD COLUMN last_integrity_check_date TIMESTAMP
                """))

                # Create index for better query performance
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_scan_results_last_integrity_check
                    ON scan_results(last_integrity_check_date)
                """))

                conn.commit()
                logger.info("Migration completed: last_integrity_check_date column and index added successfully")
            else:
                logger.debug("Migration already applied: last_integrity_check_date column exists")

    except Exception as e:
        logger.error(f"Migration v2.4.113 failed: {e}")
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

    # Sync SCAN_PATHS from environment to database
    # This allows celery-worker to read paths from DB instead of needing env var
    sync_scan_paths_to_db()

    # Use Redis-based distributed lock for cross-container scheduler coordination
    # File locks don't work across containers (each has separate /tmp filesystem)
    from pixelprobe.progress_utils import get_redis_client
    from datetime import datetime, timezone
    import socket

    redis_client = get_redis_client()
    scheduler_initialized = [False]  # Use list for mutable reference in nested functions

    # Get container hostname for lock ownership detection
    # When container restarts with same hostname, we can detect stale self-locks
    container_hostname = socket.gethostname()

    # Helper functions for scheduler lock management
    def parse_scheduler_lock(lock_value: str) -> tuple:
        """Parse lock value into (hostname, pid, timestamp_str).

        Lock formats:
        - New: "hostname:pid:timestamp" (e.g., "pixelprobe-app:123:2026-01-01T00:00:00+00:00")
        - Old: "pid:timestamp" (e.g., "123:2026-01-01T00:00:00+00:00")
        """
        parts = lock_value.split(':')
        # Detect format: new has 3+ parts where first is not a digit (hostname)
        if len(parts) >= 3 and not parts[0].isdigit():
            # New format: hostname:pid:timestamp
            return parts[0], parts[1], ':'.join(parts[2:])
        # Old format: pid:timestamp - no hostname
        return None, parts[0], ':'.join(parts[1:])

    def should_force_acquire_lock(lock_hostname, lock_pid, lock_age,
                                  my_hostname, my_pid, staleness_threshold=65) -> tuple:
        """Determine if we should force-acquire an existing lock.

        Returns (should_acquire: bool, reason: str).

        Decision matrix:
        - Same hostname AND same PID: self-lock (refresh/re-acquire)
        - Same hostname, different PID: sibling worker (only acquire if stale)
        - Different hostname: remote container (only acquire if stale)
        """
        if lock_hostname == my_hostname and lock_pid == my_pid:
            return True, "self-lock"
        if lock_hostname == my_hostname:
            # Sibling worker in same container - don't steal unless stale
            if lock_age > staleness_threshold:
                return True, "stale-sibling"
            return False, "active-sibling"
        # Different hostname - remote container
        if lock_age > staleness_threshold:
            return True, "stale-remote"
        return False, "active-remote"

    def start_scheduler_heartbeat(lock_key, redis_client, container_hostname):
        """Start a daemon thread that refreshes the scheduler lock every 30 seconds."""
        def heartbeat_loop():
            import time
            while True:
                try:
                    time.sleep(30)
                    refresh_value = f"{container_hostname}:{os.getpid()}:{datetime.now(timezone.utc).isoformat()}"
                    redis_client.set(lock_key, refresh_value, ex=60)
                    logger.debug(f"Refreshed scheduler lock in process {os.getpid()}")
                except Exception as e:
                    logger.warning(f"Failed to refresh scheduler lock: {e}")
                    break  # Stop refreshing if we can't connect to Redis

        import threading
        heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        logger.info(f"Started scheduler lock heartbeat thread in process {os.getpid()}")

    # Use Redis-based distributed lock for scheduler coordination
    # Any process can attempt to acquire the lock - first one wins
    # This allows gunicorn workers to run the scheduler (APScheduler works with gunicorn)
    if redis_client:
        try:
            # Use Redis SETNX for atomic lock acquisition (60-second expiry for auto-recovery)
            # Short TTL ensures stale locks from crashed containers expire quickly
            lock_key = 'pixelprobe:scheduler:lock'
            # Lock format: hostname:pid:timestamp - allows detection of stale self-locks after container restart
            lock_value = f"{container_hostname}:{os.getpid()}:{datetime.now(timezone.utc).isoformat()}"

            # NOTE: We do NOT delete existing locks on startup - the 60-second TTL handles stale locks
            # from crashed containers. Deleting unconditionally causes race conditions when multiple
            # containers start simultaneously (e.g., during deployment) as they delete each other's locks.

            # Try to set the lock (only succeeds if key doesn't exist)
            acquired = redis_client.set(lock_key, lock_value, nx=True, ex=60)

            if acquired:
                logger.info(f"Acquired Redis scheduler lock in process {os.getpid()}, initializing scheduler")
                scheduler.init_app(app)
                scheduler_initialized[0] = True
                app.scheduler_redis_lock_key = lock_key
                start_scheduler_heartbeat(lock_key, redis_client, container_hostname)
            else:
                # Check who has the lock and whether it's stale or from same process
                existing = redis_client.get(lock_key)
                if existing:
                    existing = existing.decode('utf-8') if isinstance(existing, bytes) else existing
                    try:
                        # Parse lock to extract hostname, pid, and timestamp
                        lock_hostname, lock_pid, lock_timestamp_str = parse_scheduler_lock(existing)
                        lock_timestamp = datetime.fromisoformat(lock_timestamp_str)
                        lock_age = (datetime.now(timezone.utc) - lock_timestamp).total_seconds()
                        current_pid = str(os.getpid())

                        # Determine if we should acquire the lock
                        should_acquire, reason = should_force_acquire_lock(
                            lock_hostname, lock_pid, lock_age,
                            container_hostname, current_pid
                        )

                        if should_acquire:
                            logger.warning(f"Acquiring scheduler lock (reason={reason}, age={lock_age:.0f}s, holder={existing})")
                            redis_client.set(lock_key, lock_value, ex=60)
                            logger.info(f"Acquired scheduler lock in process {os.getpid()}, initializing scheduler")
                            scheduler.init_app(app)
                            scheduler_initialized[0] = True
                            app.scheduler_redis_lock_key = lock_key
                            start_scheduler_heartbeat(lock_key, redis_client, container_hostname)
                        else:
                            logger.info(f"Scheduler lock held by sibling/remote (reason={reason}, holder={existing}, age={lock_age:.0f}s), skipping in process {os.getpid()}")

                            # Start background retry thread for stale lock recovery
                            def retry_scheduler_lock():
                                import time
                                retry_count = 0
                                max_retries = 10  # Try for ~5 minutes

                                while not scheduler_initialized[0] and retry_count < max_retries:
                                    time.sleep(30)
                                    retry_count += 1

                                    try:
                                        current_lock = redis_client.get(lock_key)
                                        if not current_lock:
                                            # Lock expired, try to acquire
                                            new_value = f"{container_hostname}:{os.getpid()}:{datetime.now(timezone.utc).isoformat()}"
                                            retry_acquired = redis_client.set(lock_key, new_value, nx=True, ex=60)
                                            if retry_acquired:
                                                logger.info(f"Retry #{retry_count}: Acquired scheduler lock, initializing scheduler")
                                                with app.app_context():
                                                    scheduler.init_app(app)
                                                scheduler_initialized[0] = True
                                                app.scheduler_redis_lock_key = lock_key
                                                start_scheduler_heartbeat(lock_key, redis_client, container_hostname)
                                                break
                                        else:
                                            # Check if lock is now stale
                                            lock_str = current_lock.decode('utf-8') if isinstance(current_lock, bytes) else current_lock
                                            retry_hostname, retry_pid, retry_ts_str = parse_scheduler_lock(lock_str)
                                            lock_ts = datetime.fromisoformat(retry_ts_str)
                                            current_age = (datetime.now(timezone.utc) - lock_ts).total_seconds()
                                            retry_current_pid = str(os.getpid())

                                            retry_should_acquire, retry_reason = should_force_acquire_lock(
                                                retry_hostname, retry_pid, current_age,
                                                container_hostname, retry_current_pid
                                            )

                                            if retry_should_acquire:
                                                new_value = f"{container_hostname}:{os.getpid()}:{datetime.now(timezone.utc).isoformat()}"
                                                redis_client.set(lock_key, new_value, ex=60)
                                                logger.info(f"Retry #{retry_count}: Acquired lock (reason={retry_reason}, age={current_age:.0f}s), initializing scheduler")
                                                with app.app_context():
                                                    scheduler.init_app(app)
                                                scheduler_initialized[0] = True
                                                app.scheduler_redis_lock_key = lock_key
                                                start_scheduler_heartbeat(lock_key, redis_client, container_hostname)
                                                break
                                            else:
                                                logger.debug(f"Retry #{retry_count}: Lock still held (reason={retry_reason}, age={current_age:.0f}s)")
                                    except Exception as retry_err:
                                        logger.warning(f"Retry #{retry_count} failed: {retry_err}")

                                if not scheduler_initialized[0]:
                                    logger.warning("Scheduler lock retry exhausted - another process must have it")

                            import threading
                            retry_thread = threading.Thread(target=retry_scheduler_lock, daemon=True)
                            retry_thread.start()
                            logger.info(f"Started scheduler lock retry thread in process {os.getpid()}")

                    except Exception as parse_err:
                        # Couldn't parse timestamp, just log and skip
                        logger.info(f"Scheduler already running (lock held by: {existing}), skipping in process {os.getpid()}")

        except Exception as e:
            logger.warning(f"Redis lock failed ({e}), falling back to file lock")
            redis_client = None

    # Fallback to file lock if Redis unavailable (for local development without Redis)
    if not redis_client and not scheduler_initialized[0]:
        import fcntl
        scheduler_lock_file = '/tmp/pixelprobe_scheduler.lock'

        try:
            lock_file = open(scheduler_lock_file, 'w')
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            logger.info(f"Acquired file scheduler lock in process {os.getpid()}, initializing scheduler (Redis unavailable)")
            scheduler.init_app(app)
            app.scheduler_lock_file = lock_file

        except (IOError, OSError) as e:
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

    # Clean up bloated scan results from pre-v2.4.213 (when warning_details stored thousands of lines)
    # Files with large scan_output or warning_details will be marked for rescan
    try:
        from models import ScanResult
        # Find records with large text fields (>50KB indicates old bloated format)
        # Using SQL length() function for efficiency
        bloated_results = db.session.query(ScanResult).filter(
            db.or_(
                db.func.length(ScanResult.scan_output) > 50000,
                db.func.length(ScanResult.warning_details) > 50000
            )
        ).all()

        if bloated_results:
            logger.info(f"Found {len(bloated_results)} scan results with bloated output fields (pre-v2.4.213 format)")
            logger.info("Deleting bloated records to trigger efficient rescan with v2.4.213+ format")

            for result in bloated_results:
                db.session.delete(result)

            db.session.commit()
            logger.info(f"Deleted {len(bloated_results)} bloated scan results - they will be rescanned with efficient storage")
        else:
            logger.debug("No bloated scan results found - database is clean")
    except Exception as e:
        logger.warning(f"Could not clean up bloated scan results on startup: {e}")
        # Not critical for app startup

if __name__ == '__main__':
    # Start the application (initialization already done above)
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        debug=os.environ.get('DEBUG', 'False').lower() == 'true'
    )
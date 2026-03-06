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
from pixelprobe.models import db
from pixelprobe.version import __version__, __github_url__
from pixelprobe.scheduler import MediaScheduler

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
from pixelprobe.auth import init_auth, auth_required

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
from pixelprobe.config import get_config
config_name = os.getenv('FLASK_ENV', 'development')
config_class = get_config(config_name)
config_class.init_app(app)

# Auto-generate INTERNAL_API_SECRET if not set via environment
# This secret is used for scheduler-to-app internal authentication
# In multi-worker/multi-container setups, the secret is shared via Redis
# so all gunicorn workers and the celery worker use the same value.
if not app.config.get('INTERNAL_API_SECRET'):
    import secrets
    _REDIS_SECRET_KEY = 'pixelprobe:internal_api_secret'
    _secret_loaded = False
    try:
        from pixelprobe.progress_utils import get_redis_client
        _redis = get_redis_client()
        if _redis:
            _existing = _redis.get(_REDIS_SECRET_KEY)
            if _existing:
                app.config['INTERNAL_API_SECRET'] = _existing.decode('utf-8') if isinstance(_existing, bytes) else _existing
                _secret_loaded = True
                logger.info("Loaded INTERNAL_API_SECRET from Redis (shared across workers)")
            else:
                _new_secret = secrets.token_urlsafe(32)
                # Use SETNX to avoid race conditions between workers
                if _redis.set(_REDIS_SECRET_KEY, _new_secret, nx=True):
                    app.config['INTERNAL_API_SECRET'] = _new_secret
                    logger.info("Generated and stored INTERNAL_API_SECRET in Redis")
                else:
                    # Another worker beat us to it, read theirs
                    _existing = _redis.get(_REDIS_SECRET_KEY)
                    app.config['INTERNAL_API_SECRET'] = _existing.decode('utf-8') if isinstance(_existing, bytes) else _existing
                    logger.info("Loaded INTERNAL_API_SECRET from Redis (set by sibling worker)")
                _secret_loaded = True
    except Exception as e:
        logger.warning(f"Could not use Redis for INTERNAL_API_SECRET: {e}")

    if not _secret_loaded:
        app.config['INTERNAL_API_SECRET'] = secrets.token_urlsafe(32)
        logger.info("Auto-generated INTERNAL_API_SECRET (Redis unavailable, single-worker mode)")

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
from pixelprobe.celery_config import create_celery, init_celery
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
    from pixelprobe.models import ScanConfiguration
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

def create_tables():
    """Initialize database tables and run migrations"""
    from pixelprobe.migrations.startup import migrate_database
    from pixelprobe.startup import cleanup_stuck_operations

    logger.info(f"Starting PixelProbe v{__version__}")
    with app.app_context():
        try:
            from sqlalchemy import inspect, exc, text

            try:
                inspector = inspect(db.engine)
                existing_tables = inspector.get_table_names()

                for table_name, table in db.metadata.tables.items():
                    if table_name not in existing_tables:
                        try:
                            table.create(db.engine)
                            logger.info(f"Created table: {table_name}")
                        except (exc.OperationalError, exc.IntegrityError, exc.ProgrammingError) as e:
                            err_str = str(e).lower()
                            if any(msg in err_str for msg in ["already exists", "duplicate key", "typname_nsp_index"]):
                                logger.debug(f"Table {table_name} already created by another worker")
                            else:
                                logger.error(f"Error creating table {table_name}: {str(e)}")

                logger.info("Database tables verified successfully")

                # Run migrations for v2.2.68 - add tracking columns if they don't exist
                if 'scan_state' in existing_tables:
                    try:
                        columns = [col['name'] for col in inspector.get_columns('scan_state')]
                        with db.engine.connect() as conn:
                            for col_name in ['num_workers', 'files_added', 'files_updated']:
                                if col_name not in columns:
                                    default = '1' if col_name == 'num_workers' else '0'
                                    try:
                                        conn.execute(text(f"ALTER TABLE scan_state ADD COLUMN {col_name} INTEGER DEFAULT {default}"))
                                        conn.commit()
                                        logger.info(f"Added {col_name} column to scan_state table")
                                    except exc.OperationalError as e:
                                        if "already exists" not in str(e).lower():
                                            logger.warning(f"Could not add {col_name} column: {e}")
                    except Exception as e:
                        logger.warning(f"Migration check failed (non-critical): {e}")

            except exc.OperationalError as e:
                if "already exists" not in str(e):
                    logger.error(f"Database operation error: {str(e)}")
                else:
                    logger.info("Tables already exist (created by another worker)")

            migrate_database(db)
            cleanup_stuck_operations(db)

        except Exception as e:
            logger.error(f"Error in database initialization: {str(e)}")

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

    # Initialize scheduler with distributed lock coordination
    from pixelprobe.scheduler_lock import initialize_scheduler_with_lock
    initialize_scheduler_with_lock(app, scheduler)

    # Clean up stale state from previous runs
    from pixelprobe.startup import cleanup_stuck_scans, cleanup_bloated_scan_results
    cleanup_stuck_scans(db)
    cleanup_bloated_scan_results(db)

if __name__ == '__main__':
    # Start the application (initialization already done above)
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        debug=os.environ.get('DEBUG', 'False').lower() == 'true'
    )
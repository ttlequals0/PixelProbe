"""
PixelProbe Application - Refactored Version
This is a demonstration of how app.py would look with the new modular architecture
"""

import os
import logging
from datetime import datetime, timezone
import pytz
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

# Get timezone
APP_TIMEZONE = os.environ.get('TZ', 'UTC')
try:
    tz = pytz.timezone(APP_TIMEZONE)
    logger.info(f"Using timezone: {APP_TIMEZONE}")
except pytz.exceptions.UnknownTimeZoneError:
    tz = pytz.UTC
    logger.warning(f"Unknown timezone '{APP_TIMEZONE}', falling back to UTC")

# Create Flask app
app = Flask(__name__)

# Configure app
# Require SECRET_KEY in production - no insecure fallback
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    logger.error("SECRET_KEY environment variable is required for security")
    raise ValueError("SECRET_KEY environment variable must be set")
app.config['SECRET_KEY'] = SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
    'pool_size': 10,
    'max_overflow': 20,
    'pool_timeout': 30,
    'connect_args': {
        'timeout': 30,
        'check_same_thread': False,
        'isolation_level': 'DEFERRED',
    }
}

# Initialize extensions
db.init_app(app)

# Apply SQLite optimizations for large databases
if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
    from pixelprobe.services.db_optimization import setup_sqlite_optimizations
    setup_sqlite_optimizations(db)

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
        'timestamp': datetime.now(tz).isoformat()
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
    """Initialize database tables"""
    with app.app_context():
        try:
            # Use inspector to check existing tables
            from sqlalchemy import inspect, exc
            
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
    from sqlalchemy import text
    from app_startup_migration import run_startup_migrations
    
    try:
        # Run startup migrations for v2.0.89
        run_startup_migrations(db)
        
        # Create performance indexes
        create_performance_indexes()
        
        # Add new columns if they don't exist
        with db.engine.connect() as conn:
            # Migrate scan_results table
            result = conn.execute(text("PRAGMA table_info(scan_results)"))
            columns = [row[1] for row in result]
            
            migrations = [
                ('has_warnings', "ALTER TABLE scan_results ADD COLUMN has_warnings BOOLEAN DEFAULT 0"),
                ('discovered_date', "ALTER TABLE scan_results ADD COLUMN discovered_date TIMESTAMP"),
                ('marked_as_good', "ALTER TABLE scan_results ADD COLUMN marked_as_good BOOLEAN DEFAULT 0"),
                ('file_exists', "ALTER TABLE scan_results ADD COLUMN file_exists BOOLEAN DEFAULT 1"),
                ('error_message', "ALTER TABLE scan_results ADD COLUMN error_message TEXT"),
                ('media_info', "ALTER TABLE scan_results ADD COLUMN media_info TEXT")
            ]
            
            for column_name, sql in migrations:
                if column_name not in columns:
                    logger.info(f"Adding {column_name} column to scan_results table")
                    conn.execute(text(sql))
                    conn.commit()
            
            # Migrate scan_configurations table
            result = conn.execute(text("PRAGMA table_info(scan_configurations)"))
            columns = [row[1] for row in result]
            
            config_migrations = [
                ('path', "ALTER TABLE scan_configurations ADD COLUMN path VARCHAR(500)"),
                ('is_active', "ALTER TABLE scan_configurations ADD COLUMN is_active BOOLEAN DEFAULT 1"),
                ('created_at', "ALTER TABLE scan_configurations ADD COLUMN created_at TIMESTAMP")
            ]
            
            for column_name, sql in config_migrations:
                if column_name not in columns:
                    logger.info(f"Adding {column_name} column to scan_configurations table")
                    conn.execute(text(sql))
                    conn.commit()
            
            # Migrate ignored_error_patterns table
            result = conn.execute(text("PRAGMA table_info(ignored_error_patterns)"))
            columns = [row[1] for row in result]
            
            if 'created_at' not in columns:
                logger.info("Adding created_at column to ignored_error_patterns table")
                conn.execute(text("ALTER TABLE ignored_error_patterns ADD COLUMN created_at TIMESTAMP"))
                conn.commit()
            
            # Migrate scan_schedules table
            result = conn.execute(text("PRAGMA table_info(scan_schedules)"))
            columns = [row[1] for row in result]
            
            schedule_migrations = [
                ('force_rescan', "ALTER TABLE scan_schedules ADD COLUMN force_rescan BOOLEAN DEFAULT 0"),
                ('created_at', "ALTER TABLE scan_schedules ADD COLUMN created_at TIMESTAMP")
            ]
            
            for column_name, sql in schedule_migrations:
                if column_name not in columns:
                    logger.info(f"Adding {column_name} column to scan_schedules table")
                    conn.execute(text(sql))
                    conn.commit()
            
            # Migrate scan_state table
            result = conn.execute(text("PRAGMA table_info(scan_state)"))
            columns = [row[1] for row in result]
            
            state_migrations = [
                ('directories', "ALTER TABLE scan_state ADD COLUMN directories TEXT"),
                ('force_rescan', "ALTER TABLE scan_state ADD COLUMN force_rescan BOOLEAN DEFAULT 0"),
                ('current_chunk_index', "ALTER TABLE scan_state ADD COLUMN current_chunk_index INTEGER DEFAULT 0"),
                ('total_chunks', "ALTER TABLE scan_state ADD COLUMN total_chunks INTEGER DEFAULT 0"),
                ('chunks_completed', "ALTER TABLE scan_state ADD COLUMN chunks_completed TEXT")
            ]
            
            for column_name, sql in state_migrations:
                if column_name not in columns:
                    logger.info(f"Adding {column_name} column to scan_state table")
                    conn.execute(text(sql))
                    conn.commit()
            
            # Check if scan_chunks table exists
            tables = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            table_names = [row[0] for row in tables]
            
            if 'scan_chunks' not in table_names:
                logger.info("Creating scan_chunks table for resumable scanning")
                conn.execute(text("""
                    CREATE TABLE scan_chunks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        scan_id VARCHAR(36) NOT NULL,
                        chunk_id VARCHAR(100) NOT NULL UNIQUE,
                        directory_path VARCHAR(500) NOT NULL,
                        phase VARCHAR(20) NOT NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'pending',
                        files_discovered INTEGER NOT NULL DEFAULT 0,
                        files_added INTEGER NOT NULL DEFAULT 0,
                        files_scanned INTEGER NOT NULL DEFAULT 0,
                        start_time DATETIME,
                        end_time DATETIME,
                        error_message TEXT
                    )
                """))
                conn.execute(text("CREATE INDEX idx_scan_chunks_scan_id ON scan_chunks(scan_id)"))
                conn.execute(text("CREATE INDEX idx_scan_chunks_chunk_id ON scan_chunks(chunk_id)"))
                conn.execute(text("CREATE INDEX idx_scan_chunks_status ON scan_chunks(status)"))
                conn.commit()
        
        logger.info("Database migration completed successfully")
        
    except Exception as e:
        logger.error(f"Error during database migration: {e}")

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
    create_tables()
    init_services()
    scheduler.init_app(app)

if __name__ == '__main__':
    # Start the application (initialization already done above)
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        debug=os.environ.get('DEBUG', 'False').lower() == 'true'
    )
"""
Pytest configuration and fixtures
"""

import pytest
import tempfile
import os
import shutil
from pathlib import Path
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# Import models first to ensure they're registered with SQLAlchemy
from pixelprobe.models import db as _db, ScanResult, ScanState, CleanupState, FileChangesState, ScanConfiguration, IgnoredErrorPattern, ScanSchedule, ScanReport, Exclusion, User, APIToken, LogEntry, AppConfig

# Import models to ensure they're available
from pixelprobe.models import ScanState, ScanResult

# Add missing total_files property for ScanState (still needed for utils.py)
def _total_files(self):
    return getattr(self, 'estimated_total', 0)

ScanState.total_files = property(_total_files)

# Create a test-specific app to avoid the limiter issue
def create_test_app():
    """Create a test application without importing the main app"""
    from flask import Flask
    from flask_sqlalchemy import SQLAlchemy
    from flask_cors import CORS
    from flask_wtf.csrf import CSRFProtect

    # Set SECRET_KEY environment variable for tests (required by config.py)
    os.environ['SECRET_KEY'] = 'test-secret-key'

    test_app = Flask(__name__)
    test_app.config['TESTING'] = True
    test_app.config['SECRET_KEY'] = 'test-secret-key'
    test_app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    test_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    test_app.config['SQLALCHEMY_EXPIRE_ON_COMMIT'] = False  # Prevent detached instance errors in tests
    test_app.config['WTF_CSRF_ENABLED'] = False
    test_app.config['INTERNAL_API_SECRET'] = 'test-internal-secret'
    
    # Initialize extensions
    _db.init_app(test_app)
    CORS(test_app)
    csrf = CSRFProtect(test_app)

    # Initialize authentication
    from pixelprobe.auth import init_auth
    init_auth(test_app)
    
    # Import and register blueprints
    from pixelprobe.api.scan_routes import scan_bp
    from pixelprobe.api.stats_routes import stats_bp
    from pixelprobe.api.admin_routes import admin_bp, set_scheduler
    from pixelprobe.api.export_routes import export_bp
    from pixelprobe.api.maintenance_routes import maintenance_bp
    from pixelprobe.api.reports_routes import reports_bp
    from pixelprobe.api.scan_routes_parallel import parallel_scan_bp
    from pixelprobe.api.auth_routes import auth_bp
    from pixelprobe.api.log_routes import log_bp
    from pixelprobe.scheduler import MediaScheduler

    test_app.register_blueprint(scan_bp)
    test_app.register_blueprint(auth_bp)
    test_app.register_blueprint(stats_bp)
    test_app.register_blueprint(admin_bp)
    test_app.register_blueprint(export_bp)
    test_app.register_blueprint(maintenance_bp)
    test_app.register_blueprint(reports_bp)
    test_app.register_blueprint(parallel_scan_bp)
    test_app.register_blueprint(log_bp)

    # Exempt API endpoints from CSRF
    csrf.exempt(scan_bp)
    csrf.exempt(stats_bp)
    csrf.exempt(admin_bp)
    csrf.exempt(export_bp)
    csrf.exempt(maintenance_bp)
    csrf.exempt(reports_bp)
    csrf.exempt(parallel_scan_bp)
    csrf.exempt(auth_bp)
    csrf.exempt(log_bp)
    
    # Set up scheduler without initializing (to avoid DB access before tables exist)
    scheduler = MediaScheduler()
    scheduler.app = test_app  # Set app directly without full init
    scheduler.scheduler.start()  # Start the scheduler
    set_scheduler(scheduler)
    
    # Add basic routes (with auth_required to match production app)
    from pixelprobe.auth import auth_required as _auth_required

    @test_app.route('/health')
    @_auth_required
    def health_check():
        from datetime import datetime, timezone
        return {
            'status': 'healthy',
            'version': '1.0.0',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

    @test_app.route('/api/version')
    @_auth_required
    def get_version():
        return {
            'version': '1.0.0',
            'github_url': 'https://github.com/test/test',
            'api_version': '1.0'
        }
    
    return test_app

# Import services
from pixelprobe.services import ScanService, StatsService, MaintenanceService
from pixelprobe.repositories import ScanRepository, ConfigurationRepository

@pytest.fixture(scope='session')
def app():
    """Create application for testing"""
    test_app = create_test_app()
    
    # Initialize services for testing
    with test_app.app_context():
        test_app.scan_service = ScanService(':memory:')
        test_app.stats_service = StatsService()
        test_app.maintenance_service = MaintenanceService(':memory:')
        test_app.scan_repository = ScanRepository()
        test_app.config_repository = ConfigurationRepository()
    
    return test_app

@pytest.fixture(scope='session')
def client(app):
    """Create a test client"""
    return app.test_client()

@pytest.fixture(scope='function')
def authenticated_client(app, client):
    """Create an authenticated test client with a test user"""
    with app.app_context():
        from pixelprobe.models import db, User
        db.create_all()

        # Create a test admin user
        test_user = User(
            username='testadmin',
            email='testadmin@test.com',
            is_admin=True
        )
        test_user.set_password('testpass123')

        # Clear and add user
        try:
            User.query.filter_by(username='testadmin').delete()
            db.session.commit()
        except:
            db.session.rollback()

        db.session.add(test_user)
        db.session.commit()

    # Login happens outside app context to properly initialize session
    client.post('/api/auth/login',
               json={'username': 'testadmin', 'password': 'testpass123'})

    return client

@pytest.fixture(scope='function')
def db(app):
    """Create database for testing"""
    with app.app_context():
        # Ensure all models are loaded
        from pixelprobe.models import (ScanResult, ScanState, CleanupState, FileChangesState, 
                            ScanConfiguration, IgnoredErrorPattern, ScanSchedule, 
                            ScanReport, Exclusion, ScanChunk)
        
        _db.create_all()
        yield _db
        
        try:
            # First, mark any active cleanup as cancelled to stop background threads
            from pixelprobe.models import CleanupState, ScanState
            try:
                cleanup_states = CleanupState.query.filter_by(is_active=True).all()
                for cleanup in cleanup_states:
                    cleanup.is_active = False
                    cleanup.cancelled = True
                _db.session.commit()
            except Exception:
                pass  # Ignore errors if tables don't exist
            
            # Mark any active scans as stopped
            try:
                scan_state = ScanState.get_or_create()
                if scan_state.is_active:
                    scan_state.stop_scan()
                    _db.session.commit()
            except Exception:
                pass
            
            # Clean up any running scan threads before dropping tables
            if hasattr(app, 'scan_service') and app.scan_service:
                if hasattr(app.scan_service, 'current_scan_thread') and app.scan_service.current_scan_thread:
                    app.scan_service.scan_cancelled = True
                    if app.scan_service.current_scan_thread.is_alive():
                        app.scan_service.current_scan_thread.join(timeout=1.0)
                    app.scan_service.current_scan_thread = None
            
            # Wait for all background threads to finish
            import threading
            import time
            max_wait = 2.0  # Maximum 2 seconds wait
            start_time = time.time()
            active_threads = []
            
            while time.time() - start_time < max_wait:
                active_threads = []
                for thread in threading.enumerate():
                    if thread != threading.main_thread() and thread.is_alive():
                        thread_name = thread.name.lower() if hasattr(thread, 'name') else ''
                        if 'cleanup' in thread_name or 'scan' in thread_name:
                            active_threads.append(thread)
                
                if not active_threads:
                    break
                    
                # Give threads a moment to finish
                time.sleep(0.1)
            
            # Force join any remaining threads
            for thread in active_threads:
                if thread.is_alive():
                    thread.join(timeout=0.1)
            
            # Close all sessions before dropping tables
            _db.session.remove()
            
            # Force close the connection to release locks
            if hasattr(_db.engine, 'dispose'):
                try:
                    _db.engine.dispose()
                except Exception:
                    pass  # Ignore disposal errors
            
            # Try to drop tables, but don't fail if locked
            try:
                _db.drop_all()
            except Exception as e:
                # If tables are locked, that's okay for tests
                import logging
                logging.warning(f"Could not drop all tables during teardown: {e}")
        except Exception as e:
            # Log but don't fail teardown
            import logging
            logging.warning(f"Error during database teardown: {e}")

@pytest.fixture(scope='session')
def test_data_dir():
    """Create temporary directory with test files"""
    temp_dir = tempfile.mkdtemp()
    
    # Get the real media samples directory
    media_samples_dir = os.path.join(os.path.dirname(__file__), 'fixtures', 'media_samples')
    
    # Create test file structure
    paths = {
        'test_dir': os.path.join(temp_dir, 'test_directory')
    }
    
    # Create test directory
    os.makedirs(paths['test_dir'])
    
    # Copy real media files if they exist, otherwise create minimal test files
    if os.path.exists(media_samples_dir):
        # Use real media files - copy ALL available formats
        for filename in os.listdir(media_samples_dir):
            if filename.startswith(('valid.', 'corrupted.')) and not filename.endswith('.md'):
                src = os.path.join(media_samples_dir, filename)
                dst = os.path.join(temp_dir, filename)
                shutil.copy2(src, dst)
                # Add to paths dict without extension
                key = filename.replace('.', '_')
                paths[key] = dst
        
        # Copy large video if available, otherwise use valid.mp4
        large_src = os.path.join(media_samples_dir, 'video.mp4')
        if os.path.exists(large_src):
            paths['large_video'] = os.path.join(temp_dir, 'large_video.mp4')
            shutil.copy2(large_src, paths['large_video'])
        elif 'valid_mp4' in paths:
            paths['large_video'] = paths['valid_mp4']
    else:
        # Fallback: Create minimal valid files for testing
        paths['valid_mp4'] = os.path.join(temp_dir, 'valid.mp4')
        paths['corrupted_mp4'] = os.path.join(temp_dir, 'corrupted.mp4')
        paths['valid_jpg'] = os.path.join(temp_dir, 'valid.jpg')
        paths['corrupted_jpg'] = os.path.join(temp_dir, 'corrupted.jpg')
        paths['valid_mp3'] = os.path.join(temp_dir, 'valid.mp3')
        paths['large_video'] = os.path.join(temp_dir, 'large_video.mp4')
        
        # Valid MP4 header
        with open(paths['valid_mp4'], 'wb') as f:
            f.write(b'\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isom')
            f.write(b'\x00' * 1024)  # Padding
        
        # Corrupted MP4 (missing moov atom)
        with open(paths['corrupted_mp4'], 'wb') as f:
            f.write(b'\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isom')
            f.write(b'\xFF' * 1024)  # Invalid data
        
        # Valid JPEG header
        with open(paths['valid_jpg'], 'wb') as f:
            f.write(b'\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00')
            f.write(b'\xFF\xD9')  # End marker
        
        # Corrupted JPEG (truncated)
        with open(paths['corrupted_jpg'], 'wb') as f:
            f.write(b'\xFF\xD8\xFF\xE0')  # Incomplete header
        
        # Valid MP3 (ID3 header)
        with open(paths['valid_mp3'], 'wb') as f:
            f.write(b'ID3\x03\x00\x00\x00\x00\x00\x00')
            f.write(b'\xFF\xFB')  # MP3 sync word
        
        # Large video file (10MB)
        with open(paths['large_video'], 'wb') as f:
            f.write(b'\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isom')
            f.write(b'\x00' * (10 * 1024 * 1024))  # 10MB
    
    yield paths
    
    # Cleanup
    shutil.rmtree(temp_dir)

@pytest.fixture
def mock_scan_result(db):
    """Create a mock scan result"""
    from pixelprobe.models import ScanResult
    from datetime import datetime, timezone
    
    result = ScanResult(
        file_path='/test/video.mp4',
        file_size=1024000,
        file_type='video/mp4',
        scan_date=datetime.now(timezone.utc),
        scan_status='completed',
        is_corrupted=False,
        file_hash='abc123def456',
        marked_as_good=False
    )
    
    db.session.add(result)
    db.session.commit()
    
    return result


@pytest.fixture
def real_scan_results(db, test_data_dir):
    """Scan real media files into test database"""
    from pixelprobe.models import ScanResult
    from pixelprobe.media_checker import PixelProbe
    from datetime import datetime, timezone
    
    checker = PixelProbe()
    results = []
    
    # Scan valid files
    for key, path in test_data_dir.items():
        if key.startswith('valid_') and os.path.exists(path):
            scan_data = checker.scan_file(path)
            if scan_data:
                result = ScanResult(
                    file_path=path,
                    file_size=scan_data.get('file_size', 0),
                    file_type=scan_data.get('file_type', ''),
                    file_hash=scan_data.get('file_hash', ''),
                    scan_date=datetime.now(timezone.utc),
                    scan_status='completed',
                    is_corrupted=scan_data.get('is_corrupted', False),
                    error_message=scan_data.get('error_message'),
                    corruption_details=scan_data.get('corruption_details'),
                    scan_tool=scan_data.get('scan_tool', 'ffmpeg'),
                    scan_output=scan_data.get('scan_output'),
                    warning_details=scan_data.get('warning_details'),
                    marked_as_good=False
                )
                db.session.add(result)
                results.append(result)
    
    # Scan corrupted files
    for key, path in test_data_dir.items():
        if key.startswith('corrupted_') and os.path.exists(path):
            scan_data = checker.scan_file(path)
            if scan_data:
                result = ScanResult(
                    file_path=path,
                    file_size=scan_data.get('file_size', 0),
                    file_type=scan_data.get('file_type', ''),
                    file_hash=scan_data.get('file_hash', ''),
                    scan_date=datetime.now(timezone.utc),
                    scan_status='completed',
                    is_corrupted=scan_data.get('is_corrupted', False),
                    error_message=scan_data.get('error_message'),
                    corruption_details=scan_data.get('corruption_details'),
                    scan_tool=scan_data.get('scan_tool', 'ffmpeg'),
                    scan_output=scan_data.get('scan_output'),
                    warning_details=scan_data.get('warning_details'),
                    marked_as_good=False
                )
                db.session.add(result)
                results.append(result)
    
    db.session.commit()
    return results

@pytest.fixture
def mock_corrupted_result(db):
    """Create a mock corrupted scan result"""
    from pixelprobe.models import ScanResult
    from datetime import datetime, timezone
    
    result = ScanResult(
        file_path='/test/corrupted.mp4',
        file_size=512000,
        file_type='video/mp4',
        scan_date=datetime.now(timezone.utc),
        scan_status='completed',
        is_corrupted=True,
        corruption_details='moov atom not found',
        file_hash='xyz789',
        marked_as_good=False
    )
    
    # Set the internal attribute for the mocked property
    result._error_message = 'moov atom not found'
    
    db.session.add(result)
    db.session.commit()
    
    return result

@pytest.fixture
def mock_scan_configuration(db):
    """Create mock scan configuration"""
    from pixelprobe.models import ScanConfiguration
    from datetime import datetime, timezone
    
    # Create a configuration using the new path-based structure
    config = ScanConfiguration(
        path='/test/media',
        is_active=True,
        created_at=datetime.now(timezone.utc)
    )
    db.session.add(config)
    db.session.commit()
    
    return config
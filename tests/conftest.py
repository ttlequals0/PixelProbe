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

from models import db as _db
from app import app as _app
from pixelprobe.services import ScanService, StatsService, MaintenanceService
from pixelprobe.repositories import ScanRepository, ConfigurationRepository

@pytest.fixture(scope='session')
def app():
    """Create application for testing"""
    _app.config['TESTING'] = True
    _app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    _app.config['WTF_CSRF_ENABLED'] = False
    
    # Initialize services for testing
    with _app.app_context():
        _app.scan_service = ScanService(':memory:')
        _app.stats_service = StatsService()
        _app.maintenance_service = MaintenanceService(':memory:')
        _app.scan_repository = ScanRepository()
        _app.config_repository = ConfigurationRepository()
    
    return _app

@pytest.fixture(scope='session')
def client(app):
    """Create a test client"""
    return app.test_client()

@pytest.fixture(scope='function')
def db(app):
    """Create database for testing"""
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.remove()
        _db.drop_all()

@pytest.fixture(scope='session')
def test_data_dir():
    """Create temporary directory with test files"""
    temp_dir = tempfile.mkdtemp()
    
    # Create test file structure
    paths = {
        'valid_mp4': os.path.join(temp_dir, 'valid.mp4'),
        'corrupted_mp4': os.path.join(temp_dir, 'corrupted.mp4'),
        'valid_jpg': os.path.join(temp_dir, 'valid.jpg'),
        'corrupted_jpg': os.path.join(temp_dir, 'corrupted.jpg'),
        'valid_mp3': os.path.join(temp_dir, 'valid.mp3'),
        'large_video': os.path.join(temp_dir, 'large_video.mp4'),
        'test_dir': os.path.join(temp_dir, 'test_directory')
    }
    
    # Create test directory
    os.makedirs(paths['test_dir'])
    
    # Create minimal valid files (these would be replaced with actual test files)
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
    from models import ScanResult
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
def mock_corrupted_result(db):
    """Create a mock corrupted scan result"""
    from models import ScanResult
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
    db.session.add(result)
    db.session.commit()
    
    return result

@pytest.fixture
def mock_scan_configuration(db):
    """Create mock scan configuration"""
    from models import ScanConfiguration
    from datetime import datetime, timezone
    
    config = ScanConfiguration(
        path='/test/media',
        is_active=True,
        created_at=datetime.now(timezone.utc)
    )
    db.session.add(config)
    db.session.commit()
    
    return config
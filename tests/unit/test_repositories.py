"""
Unit tests for repository layer
"""

import pytest
from datetime import datetime, timezone

from pixelprobe.repositories import ScanRepository, ConfigurationRepository
from pixelprobe.repositories.scan_repository import ScanStateRepository
from pixelprobe.repositories.config_repository import IgnoredPatternRepository, ScheduleRepository
from models import ScanResult, ScanConfiguration, IgnoredErrorPattern, ScanSchedule, ScanState

class TestScanRepository:
    """Test the scan repository"""
    
    @pytest.fixture
    def scan_repo(self, db):
        """Create scan repository instance"""
        return ScanRepository()
    
    def test_get_by_file_path(self, scan_repo, mock_scan_result):
        """Test retrieving scan result by file path"""
        result = scan_repo.get_by_file_path('/test/video.mp4')
        
        assert result is not None
        assert result.file_path == '/test/video.mp4'
        assert result.file_size == 1024000
    
    def test_get_by_file_path_not_found(self, scan_repo):
        """Test retrieving non-existent file"""
        result = scan_repo.get_by_file_path('/nonexistent/file.mp4')
        assert result is None
    
    def test_get_stuck_scans(self, scan_repo, db):
        """Test getting stuck scans"""
        # Create stuck scans
        stuck1 = ScanResult(file_path='/stuck1.mp4', scan_status='scanning')
        stuck2 = ScanResult(file_path='/stuck2.mp4', scan_status='scanning')
        completed = ScanResult(file_path='/completed.mp4', scan_status='completed')
        
        db.session.add_all([stuck1, stuck2, completed])
        db.session.commit()
        
        stuck_scans = scan_repo.get_stuck_scans()
        
        assert len(stuck_scans) == 2
        assert all(s.scan_status == 'scanning' for s in stuck_scans)
    
    def test_reset_stuck_scans(self, scan_repo, db):
        """Test resetting stuck scans"""
        # Create stuck scans
        stuck1 = ScanResult(file_path='/stuck1.mp4', scan_status='scanning')
        stuck2 = ScanResult(file_path='/stuck2.mp4', scan_status='scanning')
        
        db.session.add_all([stuck1, stuck2])
        db.session.commit()
        
        count = scan_repo.reset_stuck_scans()
        
        assert count == 2
        assert stuck1.scan_status == 'pending'
        assert stuck2.scan_status == 'pending'
        assert stuck1.error_message == 'Reset from stuck scanning state'
    
    def test_get_corrupted_files(self, scan_repo, db, mock_scan_result, mock_corrupted_result):
        """Test getting corrupted files"""
        corrupted_files = scan_repo.get_corrupted_files()
        
        assert len(corrupted_files) == 1
        assert corrupted_files[0].is_corrupted == True
        assert corrupted_files[0].file_path == '/test/corrupted.mp4'
    
    def test_mark_files_as_good(self, scan_repo, mock_corrupted_result):
        """Test marking files as good"""
        file_ids = [mock_corrupted_result.id]
        
        count = scan_repo.mark_files_as_good(file_ids)
        
        assert count == 1
        assert mock_corrupted_result.marked_as_good == True
        assert mock_corrupted_result.is_corrupted == False
    
    def test_get_files_for_rescan(self, scan_repo, db, mock_scan_result, mock_corrupted_result):
        """Test getting files for rescan"""
        # Test getting all files
        all_files = scan_repo.get_files_for_rescan('all')
        assert len(all_files) == 2
        
        # Test getting corrupted files
        corrupted_files = scan_repo.get_files_for_rescan('corrupted')
        assert len(corrupted_files) == 1
        assert corrupted_files[0].is_corrupted == True
        
        # Test getting selected files
        selected_files = scan_repo.get_files_for_rescan('selected', file_ids=[mock_scan_result.id])
        assert len(selected_files) == 1
        assert selected_files[0].id == mock_scan_result.id
    
    def test_reset_for_rescan(self, scan_repo, mock_corrupted_result):
        """Test resetting files for rescan"""
        results = [mock_corrupted_result]
        
        count = scan_repo.reset_for_rescan(results)
        
        assert count == 1
        assert mock_corrupted_result.scan_status == 'pending'
        assert mock_corrupted_result.is_corrupted == False
        assert mock_corrupted_result.error_message is None
    
    def test_update_file_hash(self, scan_repo, mock_scan_result):
        """Test updating file hash"""
        new_hash = 'newhash123'
        new_modified = datetime.now(timezone.utc)
        
        result = scan_repo.update_file_hash(
            mock_scan_result.file_path,
            new_hash,
            new_modified
        )
        
        assert result is not None
        assert result.file_hash == new_hash
        # Compare timestamps without timezone info since SQLite may not preserve it
        assert result.last_modified.replace(tzinfo=None) == new_modified.replace(tzinfo=None)


class TestConfigurationRepository:
    """Test the configuration repository"""
    
    @pytest.fixture
    def config_repo(self, db):
        """Create configuration repository instance"""
        return ConfigurationRepository()
    
    def test_get_active_paths(self, config_repo, mock_scan_configuration):
        """Test getting active scan paths"""
        paths = config_repo.get_active_paths()
        
        assert len(paths) == 1
        assert '/test/media' in paths
    
    def test_add_new_path(self, config_repo):
        """Test adding a new scan path"""
        config = config_repo.add_path('/new/path')
        
        assert config is not None
        assert config.path == '/new/path'
        assert config.is_active == True
    
    def test_reactivate_existing_path(self, config_repo, db):
        """Test reactivating an existing path"""
        # Create inactive config
        inactive = ScanConfiguration(
            path='/old/path',
            is_active=False,
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(inactive)
        db.session.commit()
        
        # Reactivate it
        config = config_repo.add_path('/old/path')
        
        assert config.id == inactive.id
        assert config.is_active == True
    
    def test_deactivate_path(self, config_repo, mock_scan_configuration):
        """Test deactivating a scan path"""
        success = config_repo.deactivate_path('/test/media')
        
        assert success == True
        assert mock_scan_configuration.is_active == False


class TestIgnoredPatternRepository:
    """Test the ignored pattern repository"""
    
    @pytest.fixture
    def pattern_repo(self, db):
        """Create ignored pattern repository instance"""
        return IgnoredPatternRepository()
    
    def test_get_active_patterns(self, pattern_repo, db):
        """Test getting active patterns"""
        # Create patterns
        active = IgnoredErrorPattern(
            pattern='moov atom',
            description='Known issue',
            is_active=True
        )
        inactive = IgnoredErrorPattern(
            pattern='old pattern',
            description='Deprecated',
            is_active=False
        )
        db.session.add_all([active, inactive])
        db.session.commit()
        
        patterns = pattern_repo.get_active_patterns()
        
        assert len(patterns) == 1
        assert patterns[0].pattern == 'moov atom'
    
    def test_add_pattern(self, pattern_repo):
        """Test adding a new pattern"""
        pattern = pattern_repo.add_pattern('new error', 'Test pattern')
        
        assert pattern is not None
        assert pattern.pattern == 'new error'
        assert pattern.description == 'Test pattern'
        assert pattern.is_active == True
    
    def test_deactivate_pattern(self, pattern_repo, db):
        """Test deactivating a pattern"""
        # Create pattern
        pattern = IgnoredErrorPattern(
            pattern='test error',
            is_active=True
        )
        db.session.add(pattern)
        db.session.commit()
        
        success = pattern_repo.deactivate_pattern(pattern.id)
        
        assert success == True
        assert pattern.is_active == False
    
    def test_pattern_exists(self, pattern_repo, db):
        """Test checking if pattern exists"""
        # Create pattern
        pattern = IgnoredErrorPattern(
            pattern='existing error',
            is_active=True
        )
        db.session.add(pattern)
        db.session.commit()
        
        assert pattern_repo.pattern_exists('existing error') == True
        assert pattern_repo.pattern_exists('non-existing error') == False


class TestScheduleRepository:
    """Test the schedule repository"""
    
    @pytest.fixture
    def schedule_repo(self, db):
        """Create schedule repository instance"""
        return ScheduleRepository()
    
    def test_get_active_schedules(self, schedule_repo, db):
        """Test getting active schedules"""
        # Create schedules
        active = ScanSchedule(
            name='Daily scan',
            cron_expression='0 0 * * *',
            is_active=True,
            created_at=datetime.now(timezone.utc)
        )
        inactive = ScanSchedule(
            name='Old scan',
            cron_expression='0 0 * * *',
            is_active=False,
            created_at=datetime.now(timezone.utc)
        )
        db.session.add_all([active, inactive])
        db.session.commit()
        
        schedules = schedule_repo.get_active_schedules()
        
        assert len(schedules) == 1
        assert schedules[0].name == 'Daily scan'
    
    def test_create_schedule(self, schedule_repo):
        """Test creating a new schedule"""
        schedule = schedule_repo.create_schedule(
            name='Hourly scan',
            cron_expression='0 * * * *',
            scan_type='incremental',
            force_rescan=False
        )
        
        assert schedule is not None
        assert schedule.name == 'Hourly scan'
        assert schedule.scan_type == 'incremental'
        assert schedule.is_active == True
    
    def test_update_schedule(self, schedule_repo, db):
        """Test updating a schedule"""
        # Create schedule
        schedule = ScanSchedule(
            name='Test scan',
            cron_expression='0 0 * * *',
            is_active=True,
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(schedule)
        db.session.commit()
        
        # Update it
        updated = schedule_repo.update_schedule(
            schedule.id,
            name='Updated scan',
            cron_expression='0 */2 * * *'
        )
        
        assert updated is not None
        assert updated.name == 'Updated scan'
        assert updated.cron_expression == '0 */2 * * *'
    
    def test_get_schedule_by_name(self, schedule_repo, db):
        """Test getting schedule by name"""
        # Create schedule
        schedule = ScanSchedule(
            name='Named scan',
            cron_expression='0 0 * * *',
            is_active=True,
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(schedule)
        db.session.commit()
        
        found = schedule_repo.get_schedule_by_name('Named scan')
        
        assert found is not None
        assert found.id == schedule.id
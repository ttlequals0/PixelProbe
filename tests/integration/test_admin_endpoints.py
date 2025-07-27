import pytest
import json
from models import db, ScanSchedule, IgnoredErrorPattern

class TestScheduleEndpoints:
    """Test schedule management endpoints"""
    
    def test_create_schedule(self, client, app):
        """Test creating a new schedule"""
        with app.app_context():
            response = client.post('/api/schedules', 
                json={
                    'name': 'Test Schedule',
                    'cron_expression': '0 2 * * *',
                    'scan_paths': ['/test/path'],
                    'scan_type': 'full_scan'
                })
            assert response.status_code == 201
            data = response.get_json()
            assert data['message'] == 'Schedule created successfully'
            assert 'schedule_id' in data
            
            # Verify schedule was created
            schedule = ScanSchedule.query.filter_by(name='Test Schedule').first()
            assert schedule is not None
            assert schedule.cron_expression == '0 2 * * *'
    
    def test_create_schedule_duplicate_name(self, client, app):
        """Test creating schedule with duplicate name"""
        with app.app_context():
            # Create first schedule
            schedule = ScanSchedule(
                name='Existing Schedule',
                cron_expression='0 1 * * *',
                is_active=True
            )
            db.session.add(schedule)
            db.session.commit()
            
            # Try to create duplicate
            response = client.post('/api/schedules',
                json={
                    'name': 'Existing Schedule',
                    'cron_expression': '0 2 * * *'
                })
            assert response.status_code == 400
            assert 'already exists' in response.get_json()['error']
    
    def test_delete_schedule(self, client, app):
        """Test deleting a schedule"""
        with app.app_context():
            # Create schedule
            schedule = ScanSchedule(
                name='Test Delete',
                cron_expression='0 1 * * *',
                is_active=True
            )
            db.session.add(schedule)
            db.session.commit()
            schedule_id = schedule.id
            
            # Delete schedule
            response = client.delete(f'/api/schedules/{schedule_id}')
            assert response.status_code == 200
            assert 'deleted successfully' in response.get_json()['message']
            
            # Verify deleted
            assert ScanSchedule.query.get(schedule_id) is None
    
    def test_delete_nonexistent_schedule(self, client):
        """Test deleting non-existent schedule"""
        response = client.delete('/api/schedules/99999')
        assert response.status_code == 404


class TestExclusionEndpoints:
    """Test exclusion management endpoints"""
    
    def test_add_path_exclusion(self, client, monkeypatch, tmp_path):
        """Test adding a path exclusion"""
        exclusions_file = tmp_path / 'exclusions.json'
        monkeypatch.setattr('os.path.join', lambda *args: str(exclusions_file))
        
        response = client.post('/api/exclusions/path',
            json={'item': '/test/excluded/path'})
        assert response.status_code == 200
        assert 'Path added successfully' in response.get_json()['message']
        
        # Verify file was created
        assert exclusions_file.exists()
        with open(exclusions_file) as f:
            data = json.load(f)
            assert '/test/excluded/path' in data['paths']
    
    def test_add_extension_exclusion(self, client, monkeypatch, tmp_path):
        """Test adding an extension exclusion"""
        exclusions_file = tmp_path / 'exclusions.json'
        exclusions_file.write_text('{"paths": [], "extensions": []}')
        monkeypatch.setattr('os.path.join', lambda *args: str(exclusions_file))
        
        response = client.post('/api/exclusions/extension',
            json={'item': '.tmp'})
        assert response.status_code == 200
        assert 'Extension added successfully' in response.get_json()['message']
        
        with open(exclusions_file) as f:
            data = json.load(f)
            assert '.tmp' in data['extensions']
    
    def test_add_duplicate_exclusion(self, client, monkeypatch, tmp_path):
        """Test adding duplicate exclusion"""
        exclusions_file = tmp_path / 'exclusions.json'
        exclusions_file.write_text('{"paths": ["/existing"], "extensions": []}')
        monkeypatch.setattr('os.path.join', lambda *args: str(exclusions_file))
        
        response = client.post('/api/exclusions/path',
            json={'item': '/existing'})
        assert response.status_code == 400
        assert 'already exists' in response.get_json()['message']
    
    def test_remove_path_exclusion(self, client, monkeypatch, tmp_path):
        """Test removing a path exclusion"""
        exclusions_file = tmp_path / 'exclusions.json'
        exclusions_file.write_text('{"paths": ["/test/path"], "extensions": []}')
        monkeypatch.setattr('os.path.join', lambda *args: str(exclusions_file))
        
        response = client.delete('/api/exclusions/path',
            json={'item': '/test/path'})
        assert response.status_code == 200
        assert 'Path removed successfully' in response.get_json()['message']
        
        with open(exclusions_file) as f:
            data = json.load(f)
            assert '/test/path' not in data['paths']
    
    def test_remove_nonexistent_exclusion(self, client, monkeypatch, tmp_path):
        """Test removing non-existent exclusion"""
        exclusions_file = tmp_path / 'exclusions.json'
        exclusions_file.write_text('{"paths": [], "extensions": []}')
        monkeypatch.setattr('os.path.join', lambda *args: str(exclusions_file))
        
        response = client.delete('/api/exclusions/path',
            json={'item': '/nonexistent'})
        assert response.status_code == 404
        assert 'not found' in response.get_json()['error']
    
    def test_invalid_exclusion_type(self, client):
        """Test invalid exclusion type"""
        response = client.post('/api/exclusions/invalid',
            json={'item': 'test'})
        assert response.status_code == 400
        assert 'Invalid exclusion type' in response.get_json()['error']


class TestIgnoredPatternsEndpoints:
    """Test ignored patterns endpoints"""
    
    def test_add_ignored_pattern(self, client, app):
        """Test adding an ignored pattern"""
        with app.app_context():
            response = client.post('/api/ignored-patterns',
                json={
                    'pattern': 'moov atom not found',
                    'pattern_type': 'error',
                    'description': 'Test pattern'
                })
            assert response.status_code == 201
            data = response.get_json()
            assert 'pattern_id' in data
            
            # Verify pattern was created
            pattern = IgnoredErrorPattern.query.filter_by(pattern='moov atom not found').first()
            assert pattern is not None
            assert pattern.pattern_type == 'error'
    
    def test_add_duplicate_pattern(self, client, app):
        """Test adding duplicate pattern"""
        with app.app_context():
            # Create first pattern
            pattern = IgnoredErrorPattern(
                pattern='duplicate pattern',
                pattern_type='error'
            )
            db.session.add(pattern)
            db.session.commit()
            
            # Try to add duplicate
            response = client.post('/api/ignored-patterns',
                json={
                    'pattern': 'duplicate pattern',
                    'pattern_type': 'error'
                })
            assert response.status_code == 400
            assert 'already exists' in response.get_json()['error']
    
    def test_delete_ignored_pattern(self, client, app):
        """Test deleting an ignored pattern"""
        with app.app_context():
            # Create pattern
            pattern = IgnoredErrorPattern(
                pattern='test delete',
                pattern_type='error'
            )
            db.session.add(pattern)
            db.session.commit()
            pattern_id = pattern.id
            
            # Delete pattern
            response = client.delete(f'/api/ignored-patterns/{pattern_id}')
            assert response.status_code == 200
            assert 'deleted successfully' in response.get_json()['message']
            
            # Verify deleted
            assert IgnoredErrorPattern.query.get(pattern_id) is None
    
    def test_delete_nonexistent_pattern(self, client):
        """Test deleting non-existent pattern"""
        response = client.delete('/api/ignored-patterns/99999')
        assert response.status_code == 404
        assert 'not found' in response.get_json()['error']
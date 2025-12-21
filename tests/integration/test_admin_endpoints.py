import pytest
import json
from datetime import datetime, timezone, timedelta
from models import db, ScanSchedule, IgnoredErrorPattern

class TestScheduleEndpoints:
    """Test schedule management endpoints"""
    
    def test_create_schedule(self, authenticated_client, app, db):
        """Test creating a new schedule"""
        with app.app_context():
            response = authenticated_client.post('/api/schedules', 
                json={
                    'name': 'Test Schedule',
                    'cron_expression': '0 2 * * *',
                    'scan_paths': ['/test/path'],
                    'scan_type': 'full_scan'
                })
            assert response.status_code == 201
            data = response.get_json()
            assert 'id' in data  # The response is schedule.to_dict()
            assert data['name'] == 'Test Schedule'
            
            # Verify schedule was created
            schedule = ScanSchedule.query.filter_by(name='Test Schedule').first()
            assert schedule is not None
            assert schedule.cron_expression == '0 2 * * *'
    
    def test_create_schedule_duplicate_name(self, authenticated_client, app, db):
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
            response = authenticated_client.post('/api/schedules',
                json={
                    'name': 'Existing Schedule',
                    'cron_expression': '0 2 * * *'
                })
            assert response.status_code == 400
            assert 'already exists' in response.get_json()['error']
    
    def test_delete_schedule(self, authenticated_client, app, db):
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
            response = authenticated_client.delete(f'/api/schedules/{schedule_id}')
            assert response.status_code == 204
            
            # Verify hard deleted (schedule no longer exists)
            schedule = ScanSchedule.query.get(schedule_id)
            assert schedule is None
    
    def test_delete_nonexistent_schedule(self, authenticated_client, db):
        """Test deleting non-existent schedule"""
        response = authenticated_client.delete('/api/schedules/99999')
        assert response.status_code == 404
    
    def test_get_schedules(self, authenticated_client, app, db):
        """Test getting all schedules"""
        with app.app_context():
            # Create test schedules
            schedule1 = ScanSchedule(
                name='Schedule 1',
                cron_expression='0 1 * * *',
                scan_paths='["/path1"]',
                scan_type='full',
                is_active=True
            )
            schedule2 = ScanSchedule(
                name='Schedule 2',
                cron_expression='0 2 * * *',
                scan_paths='["/path2"]',
                scan_type='orphan',
                is_active=True
            )
            db.session.add(schedule1)
            db.session.add(schedule2)
            db.session.commit()
            
            # Get schedules
            response = authenticated_client.get('/api/schedules')
            assert response.status_code == 200
            data = response.get_json()
            
            # Check response format
            assert 'schedules' in data
            assert isinstance(data['schedules'], list)
            assert len(data['schedules']) == 2
            
            # Check schedule data
            names = [s['name'] for s in data['schedules']]
            assert 'Schedule 1' in names
            assert 'Schedule 2' in names
            
            # Check that scan_paths is returned as array
            for schedule in data['schedules']:
                assert isinstance(schedule['scan_paths'], list)

    def test_reactivate_schedule_updates_next_run(self, authenticated_client, app, db):
        """Test that re-enabling a disabled schedule updates next_run from stale value"""
        with app.app_context():
            # Create an inactive schedule with stale next_run (7 days in the past)
            stale_time = datetime.now(timezone.utc) - timedelta(days=7)
            schedule = ScanSchedule(
                name='Test Reactivation',
                cron_expression='*/30 * * * *',  # Every 30 minutes
                scan_paths='["/test/path"]',
                scan_type='normal',
                is_active=False,
                next_run=stale_time
            )
            db.session.add(schedule)
            db.session.commit()
            schedule_id = schedule.id
            stale_timestamp = stale_time.timestamp()

        # Re-enable the schedule via API
        response = authenticated_client.put(
            f'/api/schedules/{schedule_id}',
            json={'is_active': True}
        )

        assert response.status_code == 200
        data = response.get_json()

        # next_run should be updated (not the stale time from 7 days ago)
        assert data['next_run'] is not None
        # Parse the datetime from response
        from dateutil.parser import parse
        next_run = parse(data['next_run'])
        next_run_timestamp = next_run.timestamp()

        # The key assertion: next_run should NOT be the stale time from 7 days ago
        # It should be significantly different (at least 1 day newer)
        assert next_run_timestamp > stale_timestamp + 86400, \
            "next_run should be recalculated, not the stale value from 7 days ago"

    def test_update_cron_expression_updates_next_run(self, authenticated_client, app, db):
        """Test that changing cron expression on active schedule updates next_run"""
        with app.app_context():
            # Create an active schedule
            schedule = ScanSchedule(
                name='Test Cron Change',
                cron_expression='0 2 * * *',  # Daily at 2am
                scan_paths='["/test/path"]',
                scan_type='normal',
                is_active=True,
                next_run=datetime.now(timezone.utc) + timedelta(hours=5)
            )
            db.session.add(schedule)
            db.session.commit()
            schedule_id = schedule.id
            original_next_run = schedule.next_run

        # Change the cron expression
        response = authenticated_client.put(
            f'/api/schedules/{schedule_id}',
            json={'cron_expression': '*/30 * * * *'}  # Every 30 minutes
        )

        assert response.status_code == 200
        data = response.get_json()

        # next_run should be recalculated (within 30 minutes from now)
        assert data['next_run'] is not None
        from dateutil.parser import parse
        next_run = parse(data['next_run'])
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
        # For a */30 schedule, next_run should be within 30 minutes
        assert next_run <= datetime.now(timezone.utc) + timedelta(minutes=31)

    def test_interval_schedule_reactivation(self, authenticated_client, app, db):
        """Test that re-enabling an interval schedule updates next_run correctly"""
        with app.app_context():
            # Create an inactive interval schedule with stale next_run
            stale_time = datetime.now(timezone.utc) - timedelta(days=3)
            schedule = ScanSchedule(
                name='Test Interval Reactivation',
                cron_expression='interval:hours:6',  # Every 6 hours
                scan_paths='["/test/path"]',
                scan_type='normal',
                is_active=False,
                next_run=stale_time
            )
            db.session.add(schedule)
            db.session.commit()
            schedule_id = schedule.id
            stale_ts = stale_time.timestamp()

        # Re-enable the schedule
        response = authenticated_client.put(
            f'/api/schedules/{schedule_id}',
            json={'is_active': True}
        )

        assert response.status_code == 200
        data = response.get_json()

        # The key assertion: next_run should NOT be the stale time anymore
        # It should be in the future (regardless of timezone representation)
        assert data['next_run'] is not None
        from dateutil.parser import parse
        next_run = parse(data['next_run'])
        next_run_ts = next_run.timestamp()
        now_ts = datetime.now(timezone.utc).timestamp()

        # next_run should be in the future (not the stale time from 3 days ago)
        assert next_run_ts > now_ts, "next_run should be in the future after re-enabling"
        # next_run should definitely not be the stale time
        assert next_run_ts > stale_ts + 86400, "next_run should not be the stale time from 3 days ago"


class TestExclusionEndpoints:
    """Test exclusion management endpoints"""
    
    def test_add_path_exclusion(self, authenticated_client, db, app):
        """Test adding a path exclusion"""
        from models import Exclusion
        
        with app.app_context():
            response = authenticated_client.post('/api/exclusions/path',
                json={'item': '/test/excluded/path'})
            assert response.status_code == 200
            assert 'Path added successfully' in response.get_json()['message']
            
            # Verify exclusion was created in database
            exclusion = Exclusion.query.filter_by(
                exclusion_type='path',
                value='/test/excluded/path'
            ).first()
            assert exclusion is not None
            assert exclusion.is_active is True
    
    def test_add_extension_exclusion(self, authenticated_client, db, app):
        """Test adding an extension exclusion"""
        from models import Exclusion
        
        with app.app_context():
            response = authenticated_client.post('/api/exclusions/extension',
                json={'item': '.tmp'})
            assert response.status_code == 200
            assert 'Extension added successfully' in response.get_json()['message']
            
            # Verify exclusion was created in database
            exclusion = Exclusion.query.filter_by(
                exclusion_type='extension',
                value='.tmp'
            ).first()
            assert exclusion is not None
            assert exclusion.is_active is True
    
    def test_add_duplicate_exclusion(self, authenticated_client, db, app):
        """Test adding duplicate exclusion"""
        from models import Exclusion
        
        with app.app_context():
            # Create existing exclusion
            existing = Exclusion(
                exclusion_type='path',
                value='/existing',
                is_active=True
            )
            db.session.add(existing)
            db.session.commit()
            
            # Try to add duplicate
            response = authenticated_client.post('/api/exclusions/path',
                json={'item': '/existing'})
            assert response.status_code == 400
            assert 'already exists' in response.get_json()['error']
    
    def test_remove_path_exclusion(self, authenticated_client, db, app):
        """Test removing a path exclusion"""
        from models import Exclusion
        
        with app.app_context():
            # Create exclusion to remove
            exclusion = Exclusion(
                exclusion_type='path',
                value='/test/path',
                is_active=True
            )
            db.session.add(exclusion)
            db.session.commit()
            
            # Remove exclusion
            response = authenticated_client.delete('/api/exclusions/path',
                json={'item': '/test/path'})
            assert response.status_code == 200
            assert 'Path removed successfully' in response.get_json()['message']
            
            # Verify it was soft deleted
            exclusion = Exclusion.query.filter_by(
                exclusion_type='path',
                value='/test/path'
            ).first()
            assert exclusion is not None
            assert exclusion.is_active is False
    
    def test_remove_nonexistent_exclusion(self, authenticated_client, db):
        """Test removing non-existent exclusion"""
        response = authenticated_client.delete('/api/exclusions/path',
            json={'item': '/nonexistent'})
        assert response.status_code == 404
        assert 'not found' in response.get_json()['error']
    
    def test_invalid_exclusion_type(self, authenticated_client, db):
        """Test invalid exclusion type"""
        response = authenticated_client.post('/api/exclusions/invalid',
            json={'item': 'test'})
        assert response.status_code == 400
        assert 'Invalid exclusion type' in response.get_json()['error']


class TestIgnoredPatternsEndpoints:
    """Test ignored patterns endpoints"""
    
    def test_add_ignored_pattern(self, authenticated_client, app, db):
        """Test adding an ignored pattern"""
        with app.app_context():
            response = authenticated_client.post('/api/ignored-patterns',
                json={
                    'pattern': 'moov atom not found',
                    'description': 'Test pattern'
                })
            assert response.status_code == 201
            data = response.get_json()
            assert 'id' in data  # Response includes pattern object
            
            # Verify pattern was created
            pattern = IgnoredErrorPattern.query.filter_by(pattern='moov atom not found').first()
            assert pattern is not None
            assert pattern.description == 'Test pattern'
    
    def test_add_duplicate_pattern(self, authenticated_client, app, db):
        """Test adding duplicate pattern"""
        with app.app_context():
            # Create first pattern
            pattern = IgnoredErrorPattern(
                pattern='duplicate pattern',
                description='Test pattern'
            )
            db.session.add(pattern)
            db.session.commit()
            
            # Try to add duplicate
            response = authenticated_client.post('/api/ignored-patterns',
                json={
                    'pattern': 'duplicate pattern',
                    'description': 'Another pattern'
                })
            assert response.status_code == 400
            assert 'already exists' in response.get_json()['error']
    
    def test_delete_ignored_pattern(self, authenticated_client, app, db):
        """Test deleting an ignored pattern"""
        with app.app_context():
            # Create pattern
            pattern = IgnoredErrorPattern(
                pattern='test delete',
                description='Pattern to delete'
            )
            db.session.add(pattern)
            db.session.commit()
            pattern_id = pattern.id
            
            # Delete pattern
            response = authenticated_client.delete(f'/api/ignored-patterns/{pattern_id}')
            assert response.status_code == 200
            assert 'deleted successfully' in response.get_json()['message']
            
            # Verify soft deleted
            pattern = IgnoredErrorPattern.query.get(pattern_id)
            assert pattern is not None
            assert pattern.is_active is False
    
    def test_delete_nonexistent_pattern(self, authenticated_client, db):
        """Test deleting non-existent pattern"""
        response = authenticated_client.delete('/api/ignored-patterns/99999')
        assert response.status_code == 404
        assert 'not found' in response.get_json()['error']
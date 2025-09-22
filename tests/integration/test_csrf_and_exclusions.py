import pytest
import json
import os
from flask import current_app

class TestCSRFProtection:
    """Test CSRF protection on API endpoints"""
    
    def test_api_endpoints_csrf_exempt(self, authenticated_client, db):
        """Test that main API endpoints are CSRF exempt"""
        # Test endpoints that should work without CSRF token
        endpoints = [
            ('/api/stats', 'GET'),
            ('/api/schedules', 'GET'),
            ('/api/exclusions', 'GET'),
            ('/api/schedules', 'POST'),
            ('/api/exclusions/path', 'POST'),
            ('/api/exclusions/extension', 'POST'),
        ]
        
        for endpoint, method in endpoints:
            if method == 'GET':
                response = authenticated_client.get(endpoint)
            else:
                response = authenticated_client.post(endpoint, 
                    json={'name': 'test', 'cron_expression': '0 * * * *'} if 'schedules' in endpoint
                    else {'item': 'test'})
            
            # Should not get CSRF error (400 with "CSRF token missing")
            if response.status_code == 400:
                data = response.get_json()
                assert 'CSRF' not in str(data.get('error', ''))

    def test_swagger_endpoints_csrf_exempt(self, authenticated_client, db):
        """Test that Swagger API endpoints are CSRF exempt"""
        # Test if swagger endpoints exist and are exempt
        response = authenticated_client.get('/api/v1/docs')
        # If swagger is available, it should not return CSRF error
        if response.status_code != 404:
            assert response.status_code in [200, 301, 302]


class TestExclusionsPersistence:
    """Test exclusions persistence in database"""
    
    def test_exclusions_use_database(self, authenticated_client, app, db):
        """Test that exclusions are saved to database"""
        with app.app_context():
            from models import Exclusion
            
            # Clean up any existing exclusions
            Exclusion.query.delete()
            db.session.commit()
            
            # Add an extension exclusion
            response = authenticated_client.post('/api/exclusions/extension',
                json={'item': '.test'})
            assert response.status_code == 200
            
            # Verify in database
            exclusion = Exclusion.query.filter_by(
                exclusion_type='extension',
                value='.test',
                is_active=True
            ).first()
            assert exclusion is not None
            
            # Add a path exclusion
            response = authenticated_client.post('/api/exclusions/path',
                json={'item': '/test/path'})
            assert response.status_code == 200
            
            # Verify both exclusions persist
            response = authenticated_client.get('/api/exclusions')
            assert response.status_code == 200
            data = response.get_json()
            assert '.test' in data['extensions']
            assert '/test/path' in data['paths']
            
            # Clean up
            Exclusion.query.delete()
            db.session.commit()

    def test_exclusions_persistence_across_requests(self, authenticated_client, app, db):
        """Test that exclusions persist across multiple requests"""
        with app.app_context():
            # Add multiple exclusions
            response = authenticated_client.post('/api/exclusions/extension',
                json={'item': '.tmp'})
            assert response.status_code == 200
            
            response = authenticated_client.post('/api/exclusions/extension',
                json={'item': '.bak'})
            assert response.status_code == 200
            
            # Get exclusions multiple times
            for _ in range(3):
                response = authenticated_client.get('/api/exclusions')
                assert response.status_code == 200
                data = response.get_json()
                assert '.tmp' in data['extensions']
                assert '.bak' in data['extensions']
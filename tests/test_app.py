"""
Simple tests for the main app endpoints
"""

import pytest

def test_health_endpoint(client):
    """Test the health check endpoint"""
    response = client.get('/health')
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'healthy'

def test_version_endpoint(client):
    """Test the version endpoint"""
    response = client.get('/api/version')
    assert response.status_code == 200
    data = response.get_json()
    assert 'version' in data
    assert 'github_url' in data

def test_scan_status_endpoint(client, db):
    """Test the scan status endpoint"""
    # Ensure ScanState table exists and has a default entry
    from models import ScanState
    db.create_all()
    
    response = client.get('/api/scan-status')
    assert response.status_code == 200
    data = response.get_json()
    
    # Check required fields are present
    assert 'status' in data
    assert 'current' in data
    assert 'total' in data
    assert 'is_running' in data
    assert 'phase' in data
    
    # Check default values for idle state
    assert data['is_running'] is False
    assert data['phase'] in ['idle', 'completed', None]  # None is possible if no scan state

def test_stats_endpoint(client, db, mock_scan_result):
    """Test the stats endpoint"""
    response = client.get('/api/stats')
    assert response.status_code == 200
    data = response.get_json()
    
    assert 'total_files' in data
    assert 'corrupted_files' in data
    assert 'healthy_files' in data
    assert 'warning_files' in data
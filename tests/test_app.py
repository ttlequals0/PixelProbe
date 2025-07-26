"""
Simple tests for the main app endpoints
"""

import pytest
from unittest.mock import patch, Mock

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

@patch('app.scan_state')
def test_scan_status_endpoint(mock_scan_state, client):
    """Test the scan status endpoint"""
    mock_scan_state.copy.return_value = {
        'is_scanning': False,
        'phase': 'idle',
        'phase_number': 0,
        'phase_current': 0,
        'phase_total': 0,
        'files_processed': 0,
        'estimated_total': 0,
        'average_scan_time': 0,
        'start_time': None,
        'current_file': None,
        'discovery_count': 0,
        'adding_progress': 0,
        'progress_message': '',
        'total_phases': 3
    }
    
    response = client.get('/api/scan-status')
    assert response.status_code == 200
    data = response.get_json()
    assert data['is_scanning'] is False
    assert data['phase'] == 'idle'

def test_stats_endpoint(client, db, mock_scan_result):
    """Test the stats endpoint"""
    response = client.get('/api/stats')
    assert response.status_code == 200
    data = response.get_json()
    
    assert 'total_files' in data
    assert 'corrupted_files' in data
    assert 'healthy_files' in data
    assert 'warning_files' in data
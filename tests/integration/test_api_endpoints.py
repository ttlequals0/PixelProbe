"""
Integration tests for API endpoints
"""

import pytest
import json
from datetime import datetime

class TestScanEndpoints:
    """Test scan-related API endpoints"""
    
    def test_get_scan_results(self, client, mock_scan_result):
        """Test GET /api/scan-results endpoint"""
        response = client.get('/api/scan-results')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert 'results' in data
        assert 'total' in data
        assert 'page' in data
        assert 'per_page' in data
        
        # Should include our mock result
        assert data['total'] >= 1
        assert any(r['file_path'] == '/test/video.mp4' for r in data['results'])
    
    def test_get_scan_results_with_filters(self, client, mock_scan_result, mock_corrupted_result):
        """Test scan results with filters"""
        # Test corrupted filter
        response = client.get('/api/scan-results?is_corrupted=true')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert all(r['is_corrupted'] == True for r in data['results'])
        
        # Test healthy filter
        response = client.get('/api/scan-results?is_corrupted=false')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert all(r['is_corrupted'] == False or r['marked_as_good'] == True 
                  for r in data['results'])
    
    def test_get_single_scan_result(self, client, mock_scan_result):
        """Test GET /api/scan-results/<id> endpoint"""
        response = client.get(f'/api/scan-results/{mock_scan_result.id}')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert data['id'] == mock_scan_result.id
        assert data['file_path'] == mock_scan_result.file_path
    
    def test_scan_status(self, client):
        """Test GET /api/scan-status endpoint"""
        response = client.get('/api/scan-status')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert 'status' in data
        assert 'current' in data
        assert 'total' in data
        assert 'is_running' in data


class TestStatsEndpoints:
    """Test statistics API endpoints"""
    
    def test_get_stats(self, client, mock_scan_result):
        """Test GET /api/stats endpoint"""
        response = client.get('/api/stats')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert 'total_files' in data
        assert 'completed_files' in data
        assert 'corrupted_files' in data
        assert 'healthy_files' in data
        
        # Should count our mock data
        assert data['total_files'] >= 1
    
    def test_get_system_info(self, client):
        """Test GET /api/system-info endpoint"""
        response = client.get('/api/system-info')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert 'version' in data
        assert 'database' in data
        assert 'monitored_paths' in data
        assert 'features' in data


class TestAdminEndpoints:
    """Test admin/configuration API endpoints"""
    
    def test_mark_as_good(self, client, mock_corrupted_result):
        """Test POST /api/mark-as-good endpoint"""
        response = client.post('/api/mark-as-good',
                             json={'file_ids': [mock_corrupted_result.id]})
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert data['marked_files'] == 1
        
        # Verify file was marked as good
        assert mock_corrupted_result.marked_as_good == True
        assert mock_corrupted_result.is_corrupted == False
    
    def test_get_configurations(self, client, mock_scan_configuration):
        """Test GET /api/configurations endpoint"""
        response = client.get('/api/configurations')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any(c['path'] == '/test/media' for c in data)
    
    def test_add_configuration(self, client):
        """Test POST /api/configurations endpoint"""
        response = client.post('/api/configurations',
                             json={'path': '/new/test/path'})
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert data['path'] == '/new/test/path'


class TestExportEndpoints:
    """Test export API endpoints"""
    
    def test_export_csv(self, client, mock_scan_result):
        """Test POST /api/export-csv endpoint"""
        response = client.post('/api/export-csv', json={})
        assert response.status_code == 200
        assert response.content_type == 'text/csv; charset=utf-8'
        
        # Check CSV content
        csv_data = response.data.decode('utf-8')
        assert 'File Path' in csv_data
        assert mock_scan_result.file_path in csv_data
    
    def test_export_csv_with_filters(self, client, mock_corrupted_result):
        """Test CSV export with filters"""
        response = client.post('/api/export-csv',
                             json={'filter': 'corrupted'})
        assert response.status_code == 200
        
        csv_data = response.data.decode('utf-8')
        assert mock_corrupted_result.file_path in csv_data


class TestMaintenanceEndpoints:
    """Test maintenance API endpoints"""
    
    def test_cleanup_status(self, client):
        """Test GET /api/cleanup-status endpoint"""
        response = client.get('/api/cleanup-status')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert 'is_running' in data
        assert 'phase' in data
        assert 'progress_percentage' in data
    
    def test_file_changes_status(self, client):
        """Test GET /api/file-changes-status endpoint"""
        response = client.get('/api/file-changes-status')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert 'is_running' in data
        assert 'phase' in data
        assert 'files_processed' in data


class TestHealthEndpoints:
    """Test health and version endpoints"""
    
    def test_health_check(self, client):
        """Test GET /health endpoint"""
        response = client.get('/health')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert data['status'] == 'healthy'
        assert 'version' in data
        assert 'timestamp' in data
    
    def test_version(self, client):
        """Test GET /api/version endpoint"""
        response = client.get('/api/version')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert 'version' in data
        assert 'github_url' in data
        assert 'api_version' in data
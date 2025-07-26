"""
Tests for bulk report operations
"""
import pytest
import json
import os
from datetime import datetime, timezone
import tempfile
import zipfile
import io

# Set required environment variables for testing
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

from app import app, db
from models import ScanResult, ScanReport


class TestBulkReportOperations:
    """Test bulk report download functionality"""
    
    @pytest.fixture
    def client(self):
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        
        with app.test_client() as client:
            with app.app_context():
                db.create_all()
                # Create test data
                self._create_test_reports()
                yield client
                db.drop_all()
    
    def _create_test_reports(self):
        """Create test scan reports"""
        # Create scan results first
        for i in range(5):
            result = ScanResult(
                file_path=f'/test/file{i}.mp4',
                file_size=1024 * (i + 1),
                file_type='video/mp4',
                is_corrupted=i % 2 == 0,
                scan_date=datetime.now(timezone.utc)
            )
            db.session.add(result)
        
        # Create scan reports
        for i in range(3):
            report = ScanReport(
                report_id=f'test_report_{i}',
                scan_type='full_scan',
                status='completed',
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                total_files_discovered=5,
                files_scanned=5,
                files_corrupted=2,
                files_with_warnings=1
            )
            db.session.add(report)
        
        db.session.commit()
    
    def test_download_multiple_reports_as_zip(self, client):
        """Test downloading multiple reports as ZIP"""
        # Get available reports
        response = client.get('/api/scan-reports')
        assert response.status_code == 200
        reports = response.json['reports']
        
        # Generate expected filenames based on report IDs
        filenames = []
        for report in reports[:2]:
            # Generate filename based on scan type and report ID
            if report['scan_type'] == 'cleanup':
                filename = f"cleanup_report_{report['report_id']}.json"
            else:
                filename = f"scan_report_{report['report_id']}.json"
            filenames.append(filename)
        
        # Download as ZIP
        response = client.post('/api/reports/download-multiple', 
                             json={'filenames': filenames, 'format': 'zip'})
        
        assert response.status_code == 200
        assert response.content_type == 'application/zip'
        
        # Verify ZIP contents
        zip_data = io.BytesIO(response.data)
        with zipfile.ZipFile(zip_data, 'r') as zf:
            assert len(zf.namelist()) == 2
            for filename in filenames:
                assert filename in zf.namelist()
    
    def test_download_multiple_reports_as_pdf(self, client):
        """Test downloading multiple reports as combined PDF"""
        # Get available reports
        response = client.get('/api/scan-reports')
        assert response.status_code == 200
        reports = response.json['reports']
        
        # Generate expected filenames based on report IDs
        filenames = []
        for report in reports[:2]:
            # Generate filename based on scan type and report ID
            if report['scan_type'] == 'cleanup':
                filename = f"cleanup_report_{report['report_id']}.json"
            else:
                filename = f"scan_report_{report['report_id']}.json"
            filenames.append(filename)
        
        # Download as PDF
        response = client.post('/api/reports/download-multiple', 
                             json={'filenames': filenames, 'format': 'pdf'})
        
        assert response.status_code == 200
        assert response.content_type == 'application/pdf'
        assert len(response.data) > 0  # PDF should have content
    
    def test_download_multiple_reports_missing_filenames(self, client):
        """Test error handling for missing filenames"""
        response = client.post('/api/reports/download-multiple', 
                             json={'format': 'zip'})
        
        assert response.status_code == 400
        assert 'filenames' in response.json['error']
    
    def test_download_multiple_reports_empty_list(self, client):
        """Test error handling for empty filenames list"""
        response = client.post('/api/reports/download-multiple', 
                             json={'filenames': [], 'format': 'zip'})
        
        assert response.status_code == 400
        assert 'No filenames provided' in response.json['error']
    
    def test_download_multiple_reports_invalid_format(self, client):
        """Test error handling for invalid format"""
        response = client.post('/api/reports/download-multiple', 
                             json={'filenames': ['test.json'], 'format': 'invalid'})
        
        assert response.status_code == 400
        assert 'Invalid format' in response.json['error']
"""
Tests for bulk report operations
"""
import pytest
import json
from datetime import datetime, timezone
import tempfile
import zipfile
import io
from models import ScanResult, ScanReport


class TestBulkReportOperations:
    """Test bulk report download functionality"""
    
    def _create_test_reports(self, db):
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
    
    def test_download_multiple_reports_as_zip(self, client, db):
        """Test downloading multiple reports as ZIP"""
        # Create test data
        self._create_test_reports(db)
        
        # Get available reports
        response = client.get('/api/scan-reports')
        assert response.status_code == 200
        reports = response.json['reports']
        
        # Get report IDs
        report_ids = [report['report_id'] for report in reports[:2]]
        
        # Download as ZIP
        response = client.post('/api/reports/download-multiple', 
                             json={'report_ids': report_ids, 'format': 'zip'})
        
        assert response.status_code == 200
        assert response.content_type == 'application/zip'
        
        # Verify ZIP contents
        zip_data = io.BytesIO(response.data)
        with zipfile.ZipFile(zip_data, 'r') as zf:
            assert len(zf.namelist()) == 2
            # Check that files have expected names
            for report in reports[:2]:
                if report['scan_type'] == 'cleanup':
                    expected_filename = f"cleanup_report_{report['report_id']}.json"
                else:
                    expected_filename = f"scan_report_{report['report_id']}.json"
                assert expected_filename in zf.namelist()
    
    def test_download_multiple_reports_as_pdf(self, client, db):
        """Test downloading multiple reports as combined PDF"""
        # Create test data
        self._create_test_reports(db)
        
        # Get available reports
        response = client.get('/api/scan-reports')
        assert response.status_code == 200
        reports = response.json['reports']
        
        # Get report IDs
        report_ids = [report['report_id'] for report in reports[:2]]
        
        # Download as PDF
        response = client.post('/api/reports/download-multiple', 
                             json={'report_ids': report_ids, 'format': 'pdf'})
        
        assert response.status_code == 200
        assert response.content_type == 'application/pdf'
        assert len(response.data) > 0  # PDF should have content
    
    def test_download_multiple_reports_missing_report_ids(self, client, db):
        """Test error handling for missing report IDs"""
        response = client.post('/api/reports/download-multiple', 
                             json={'format': 'zip'})
        
        assert response.status_code == 400
        assert 'report_ids' in response.json['error']
    
    def test_download_multiple_reports_empty_list(self, client, db):
        """Test error handling for empty report IDs list"""
        response = client.post('/api/reports/download-multiple', 
                             json={'report_ids': [], 'format': 'zip'})
        
        assert response.status_code == 400
        assert 'No report IDs provided' in response.json['error']
    
    def test_download_multiple_reports_invalid_report_ids(self, client, db):
        """Test error handling for invalid report IDs"""
        response = client.post('/api/reports/download-multiple', 
                             json={'report_ids': ['invalid-id-1', 'invalid-id-2'], 'format': 'zip'})
        
        assert response.status_code == 404
        assert 'No valid reports found' in response.json['error']
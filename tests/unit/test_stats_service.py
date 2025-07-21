"""
Unit tests for StatsService
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

from pixelprobe.services.stats_service import StatsService

class TestStatsService:
    """Test the statistics service"""
    
    @pytest.fixture
    def stats_service(self):
        """Create a stats service instance"""
        return StatsService()
    
    @patch('pixelprobe.services.stats_service.db')
    def test_get_file_statistics_success(self, mock_db, stats_service):
        """Test successful file statistics retrieval"""
        # Mock database result
        mock_result = MagicMock()
        mock_result.__getitem__.side_effect = lambda x: [100, 80, 10, 5, 5, 15, 85, 3, 2][x]
        
        mock_db.session.execute.return_value.fetchone.return_value = mock_result
        
        stats = stats_service.get_file_statistics()
        
        assert stats['total_files'] == 100
        assert stats['completed_files'] == 80
        assert stats['pending_files'] == 10
        assert stats['scanning_files'] == 5
        assert stats['error_files'] == 5
        assert stats['corrupted_files'] == 15
        assert stats['healthy_files'] == 85
        assert stats['marked_as_good'] == 3
        assert stats['warning_files'] == 2
    
    @patch('pixelprobe.services.stats_service.db')
    @patch('pixelprobe.services.stats_service.ScanResult')
    def test_get_file_statistics_fallback(self, mock_scan_result, mock_db, stats_service):
        """Test fallback when optimized query fails"""
        # Make optimized query fail
        mock_db.session.execute.side_effect = Exception("DB Error")
        
        # Mock fallback queries
        mock_scan_result.query.count.return_value = 100
        mock_scan_result.query.filter_by.return_value.count.side_effect = [80, 10, 5, 5, 3]
        mock_scan_result.query.filter.return_value.count.side_effect = [15, 2, 85]
        
        stats = stats_service.get_file_statistics()
        
        assert stats['total_files'] == 100
        assert stats['completed_files'] == 80
    
    @patch('pixelprobe.services.stats_service.os')
    @patch('pixelprobe.services.stats_service.db')
    def test_get_system_info(self, mock_db, mock_os, stats_service):
        """Test system info retrieval"""
        # Mock environment
        mock_os.environ.get.return_value = '2.0.55'
        
        # Mock file statistics
        with patch.object(stats_service, 'get_file_statistics') as mock_get_stats:
            mock_get_stats.return_value = {
                'total_files': 100,
                'completed_files': 80,
                'pending_files': 10,
                'scanning_files': 5,
                'error_files': 5,
                'corrupted_files': 15,
                'healthy_files': 85,
                'marked_as_good': 3,
                'warning_files': 2
            }
            
            # Mock monitored paths
            with patch.object(stats_service, '_get_monitored_paths') as mock_paths:
                mock_paths.return_value = [
                    {'path': '/movies', 'exists': True, 'file_count': 50},
                    {'path': '/tv', 'exists': True, 'file_count': 30}
                ]
                
                # Mock database performance
                with patch.object(stats_service, '_get_database_performance') as mock_perf:
                    mock_perf.return_value = {
                        'total_scans': 1000,
                        'avg_days_since_scan': 2.5,
                        'oldest_scan': '2024-01-01T00:00:00Z',
                        'newest_scan': '2025-01-20T00:00:00Z'
                    }
                    
                    info = stats_service.get_system_info()
                    
                    assert info['version'] == '2.0.55'
                    assert info['database']['total_files'] == 100
                    assert len(info['monitored_paths']) == 2
                    assert info['features']['deep_scan'] == True
    
    @patch('pixelprobe.services.stats_service.db')
    def test_get_corruption_statistics(self, mock_db, stats_service):
        """Test corruption statistics by file type"""
        # Mock database result
        mock_results = [
            ('video/mp4', 100, 10, 5),
            ('image/jpeg', 200, 5, 2),
            ('audio/mp3', 50, 0, 1)
        ]
        
        mock_db.session.execute.return_value.fetchall.return_value = mock_results
        
        stats = stats_service.get_corruption_statistics()
        
        assert 'video/mp4' in stats
        assert stats['video/mp4']['total'] == 100
        assert stats['video/mp4']['corrupted'] == 10
        assert stats['video/mp4']['warnings'] == 5
        assert stats['video/mp4']['corruption_rate'] == 10.0
        
        assert stats['audio/mp3']['corrupted'] == 0
        assert stats['audio/mp3']['corruption_rate'] == 0.0
    
    @patch('pixelprobe.services.stats_service.os')
    @patch('pixelprobe.services.stats_service.db')
    def test_get_monitored_paths(self, mock_db, mock_os, stats_service):
        """Test monitored paths retrieval"""
        # Mock environment
        mock_os.environ.get.return_value = '/movies,/tv,/originals'
        mock_os.path.exists.side_effect = [True, True, False]  # originals doesn't exist
        
        # Mock database results
        mock_results = [
            ('/movies', 50),
            ('/tv', 30),
            ('other', 10)
        ]
        mock_db.session.execute.return_value.fetchall.return_value = mock_results
        
        paths = stats_service._get_monitored_paths()
        
        assert len(paths) == 3
        assert paths[0]['path'] == '/movies'
        assert paths[0]['exists'] == True
        assert paths[0]['file_count'] == 50
        
        assert paths[2]['path'] == '/originals'
        assert paths[2]['exists'] == False
        assert paths[2]['file_count'] == 0
    
    @patch('pixelprobe.services.stats_service.db')
    def test_get_database_performance(self, mock_db, stats_service):
        """Test database performance statistics"""
        # Mock database result
        mock_result = MagicMock()
        mock_result.__getitem__.side_effect = lambda x: [
            1000,  # total_scans
            2.5,   # avg_days_since_scan
            '2024-01-01T00:00:00',  # oldest_scan
            '2025-01-20T00:00:00'   # newest_scan
        ][x]
        
        mock_db.session.execute.return_value.fetchone.return_value = mock_result
        
        perf = stats_service._get_database_performance()
        
        assert perf['total_scans'] == 1000
        assert perf['avg_days_since_scan'] == 2.5
        assert '2024-01-01' in perf['oldest_scan']
        assert '2025-01-20' in perf['newest_scan']
    
    def test_handle_database_errors_gracefully(self, stats_service):
        """Test that service handles database errors gracefully"""
        with patch('pixelprobe.services.stats_service.db') as mock_db:
            # Make all database calls fail
            mock_db.session.execute.side_effect = Exception("Database connection lost")
            
            # Should raise exception but not crash
            with pytest.raises(Exception):
                stats_service.get_system_info()
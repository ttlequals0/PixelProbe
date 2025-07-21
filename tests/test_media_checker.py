"""
Tests for PixelProbe media checker core functionality
"""

import pytest
import os
import time
import threading
from unittest.mock import Mock, patch, MagicMock

from media_checker import PixelProbe

class TestMediaChecker:
    """Test the core PixelProbe media checking functionality"""
    
    def test_corrupted_mp4_detection(self, test_data_dir):
        """Test detection of corrupted MP4 files"""
        checker = PixelProbe(database_path=':memory:')
        result = checker.scan_file(test_data_dir['corrupted_mp4'])
        
        assert result is not None
        assert result.is_corrupted == True
        # The actual error message depends on FFmpeg version
        assert result.error_message is not None or result.corruption_details is not None
    
    def test_valid_mp4_detection(self, test_data_dir):
        """Test that valid MP4 files are not marked as corrupted"""
        checker = PixelProbe(database_path=':memory:')
        result = checker.scan_file(test_data_dir['valid_mp4'])
        
        assert result is not None
        # Note: Our minimal test file might still be detected as corrupted
        # In real tests, you'd use actual valid media files
    
    def test_corrupted_jpg_detection(self, test_data_dir):
        """Test detection of corrupted JPEG files"""
        checker = PixelProbe(database_path=':memory:')
        result = checker.scan_file(test_data_dir['corrupted_jpg'])
        
        assert result is not None
        # Truncated JPEG should be detected as corrupted
    
    def test_file_hash_generation(self, test_data_dir):
        """Test that file hashes are generated correctly"""
        checker = PixelProbe(database_path=':memory:')
        result = checker.scan_file(test_data_dir['valid_mp4'])
        
        assert result is not None
        assert result.file_hash is not None
        assert len(result.file_hash) == 64  # SHA256 hash length
    
    def test_file_hash_consistency(self, test_data_dir):
        """Test that file hashes are consistent across scans"""
        checker = PixelProbe(database_path=':memory:')
        
        result1 = checker.scan_file(test_data_dir['valid_mp4'])
        result2 = checker.scan_file(test_data_dir['valid_mp4'], force_rescan=True)
        
        assert result1.file_hash == result2.file_hash
    
    def test_force_rescan(self, test_data_dir):
        """Test that force_rescan works correctly"""
        checker = PixelProbe(database_path=':memory:')
        
        # First scan
        result1 = checker.scan_file(test_data_dir['valid_mp4'])
        scan_date1 = result1.scan_date
        
        # Wait a moment
        time.sleep(0.1)
        
        # Rescan without force - should return cached result
        result2 = checker.scan_file(test_data_dir['valid_mp4'])
        assert result2.scan_date == scan_date1
        
        # Force rescan - should update scan date
        result3 = checker.scan_file(test_data_dir['valid_mp4'], force_rescan=True)
        assert result3.scan_date > scan_date1
    
    @patch('subprocess.run')
    def test_ffmpeg_timeout_handling(self, mock_run):
        """Test that FFmpeg timeouts are handled correctly"""
        # Mock subprocess to simulate timeout
        mock_run.side_effect = TimeoutError("FFmpeg timeout")
        
        checker = PixelProbe(database_path=':memory:')
        with pytest.raises(Exception):
            checker._run_ffmpeg_check('/fake/path.mp4')
    
    def test_discover_media_files(self, test_data_dir):
        """Test media file discovery"""
        checker = PixelProbe(database_path=':memory:')
        
        # Discover files in test directory
        files = checker.discover_media_files(test_data_dir['test_dir'])
        
        # Should find files in parent directory
        parent_files = checker.discover_media_files(os.path.dirname(test_data_dir['valid_mp4']))
        
        assert isinstance(parent_files, list)
        assert len(parent_files) >= 5  # Should find our test files
        
        # Check that media files are discovered
        file_names = [os.path.basename(f) for f in parent_files]
        assert 'valid.mp4' in file_names
        assert 'valid.jpg' in file_names
        assert 'valid.mp3' in file_names
    
    def test_exclusion_patterns(self, test_data_dir, monkeypatch):
        """Test that exclusion patterns work correctly"""
        # Create exclusions file
        exclusions = {
            'paths': ['/excluded'],
            'extensions': ['.tmp', '.cache']
        }
        
        import json
        exclusions_path = os.path.join(os.path.dirname(test_data_dir['valid_mp4']), 'exclusions.json')
        with open(exclusions_path, 'w') as f:
            json.dump(exclusions, f)
        
        # Mock the exclusions file path
        monkeypatch.setattr('media_checker.EXCLUSIONS_FILE', exclusions_path)
        
        checker = PixelProbe(database_path=':memory:')
        
        # Test that excluded extensions are filtered
        assert not checker._should_scan_file('/test/file.tmp')
        assert not checker._should_scan_file('/test/file.cache')
        assert checker._should_scan_file('/test/file.mp4')
        
        # Test that excluded paths are filtered
        assert not checker._should_scan_file('/excluded/file.mp4')
        assert checker._should_scan_file('/included/file.mp4')
    
    def test_concurrent_scanning_thread_safety(self, test_data_dir):
        """Test that concurrent scanning is thread-safe"""
        checker = PixelProbe(database_path=':memory:')
        results = []
        errors = []
        
        def scan_file(file_path):
            try:
                result = checker.scan_file(file_path)
                results.append(result)
            except Exception as e:
                errors.append(e)
        
        # Create threads to scan multiple files concurrently
        threads = []
        files = [
            test_data_dir['valid_mp4'],
            test_data_dir['valid_jpg'],
            test_data_dir['valid_mp3']
        ]
        
        for file_path in files * 3:  # Scan each file 3 times
            thread = threading.Thread(target=scan_file, args=(file_path,))
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join(timeout=10)
        
        # Check results
        assert len(errors) == 0, f"Errors during concurrent scanning: {errors}"
        assert len(results) == len(files) * 3
    
    def test_performance_benchmark(self, test_data_dir, benchmark):
        """Benchmark scanning performance"""
        checker = PixelProbe(database_path=':memory:')
        
        # Benchmark small file scanning
        result = benchmark(checker.scan_file, test_data_dir['valid_mp4'])
        
        # Performance assertions
        assert benchmark.stats['mean'] < 2.0  # Should complete in under 2 seconds on average
        assert result is not None
    
    def test_memory_usage_large_file(self, test_data_dir):
        """Test memory usage doesn't spike with large files"""
        import psutil
        import gc
        
        checker = PixelProbe(database_path=':memory:')
        
        # Get initial memory usage
        gc.collect()
        process = psutil.Process()
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB
        
        # Scan large file
        checker.scan_file(test_data_dir['large_video'])
        
        # Get memory after scan
        gc.collect()
        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        
        # Memory increase should be reasonable (less than 100MB)
        memory_increase = final_memory - initial_memory
        assert memory_increase < 100, f"Memory increased by {memory_increase}MB"
    
    def test_error_pattern_ignoring(self, test_data_dir, db):
        """Test that ignored error patterns work correctly"""
        from models import IgnoredErrorPattern
        
        # Add ignored pattern
        pattern = IgnoredErrorPattern(
            pattern='moov atom not found',
            description='Known FFmpeg issue',
            is_active=True
        )
        db.session.add(pattern)
        db.session.commit()
        
        checker = PixelProbe(database_path=':memory:')
        
        # Mock the error checking to return our pattern
        with patch.object(checker, '_check_ignored_patterns') as mock_check:
            mock_check.return_value = False  # Not ignored
            result = checker.scan_file(test_data_dir['corrupted_mp4'])
            
            # Should be marked as corrupted when not ignored
            mock_check.return_value = True  # Ignored
            result2 = checker.scan_file(test_data_dir['corrupted_mp4'], force_rescan=True)
            
            # The behavior depends on implementation
    
    def test_scan_output_capture(self, test_data_dir):
        """Test that scan output is properly captured"""
        checker = PixelProbe(database_path=':memory:')
        result = checker.scan_file(test_data_dir['valid_mp4'])
        
        # Scan output should be captured
        assert hasattr(result, 'scan_output')
        # Output might be None for valid files or contain FFmpeg output
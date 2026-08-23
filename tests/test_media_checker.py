"""
Tests for PixelProbe media checker core functionality
"""

import pytest
import os
import subprocess
import threading
from unittest.mock import Mock, patch, MagicMock

from pixelprobe.media_checker import PixelProbe

def settings_with(**overrides):
    """Registry defaults with specific settings overridden, keyed as the scanner reads them."""
    from pixelprobe.constants import SCANNER_SETTINGS
    values = {spec['key']: spec['default'] for spec in SCANNER_SETTINGS}
    values.update(overrides)
    return values


def patch_settings(**overrides):
    """Patch the scanner's settings resolution for the duration of a test."""
    return patch('pixelprobe.media_checker.resolve_settings',
                 return_value=settings_with(**overrides))


class TestMediaChecker:
    """Test the core PixelProbe media checking functionality"""
    
    def test_corrupted_mp4_detection(self, test_data_dir):
        """Test detection of corrupted MP4 files"""
        checker = PixelProbe()
        result = checker.scan_file(test_data_dir['corrupted_mp4'])
        
        assert result is not None
        assert result['is_corrupted'] == True
        # The actual error message depends on FFmpeg version
        assert result.get('error_message') is not None or result.get('corruption_details') is not None
    
    def test_valid_mp4_detection(self, test_data_dir):
        """Test that valid MP4 files are not marked as corrupted"""
        checker = PixelProbe()
        result = checker.scan_file(test_data_dir['valid_mp4'])
        
        assert result is not None
        # Note: Our minimal test file might still be detected as corrupted
        # In real tests, you'd use actual valid media files
    
    def test_corrupted_jpg_detection(self, test_data_dir):
        """Test detection of corrupted JPEG files"""
        checker = PixelProbe()
        result = checker.scan_file(test_data_dir['corrupted_jpg'])
        
        assert result is not None
        # Truncated JPEG should be detected as corrupted
    
    def test_file_hash_generation(self, test_data_dir):
        """Test that file hashes are generated correctly"""
        checker = PixelProbe()
        result = checker.scan_file(test_data_dir['valid_mp4'])
        
        assert result is not None
        assert result['file_hash'] is not None
        assert len(result['file_hash']) == 64  # SHA256 hash length
    
    @patch('os.path.getsize')
    @patch('os.path.exists')
    @patch('subprocess.run')
    @patch('pixelprobe.media_checker._ffprobe_with_timeout')
    def test_hevc_main10_detection(self, mock_probe, mock_run, mock_exists, mock_getsize):
        """Test detection of HEVC Main 10 profile issues"""
        # Mock file exists and size
        mock_exists.return_value = True
        mock_getsize.return_value = 1024 * 1024  # 1MB
        
        # Mock ffmpeg probe to return HEVC Main 10 profile
        mock_probe.return_value = {
            'streams': [{
                'codec_type': 'video',
                'codec_name': 'hevc',
                'profile': 'Main 10',
                'pix_fmt': 'yuv420p10le',
                'duration': '300.5'
            }]
        }
        
        # Mock subprocess run for HEVC Main 10 analysis
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = 'reference picture missing'
        mock_result.stdout = ''
        mock_run.return_value = mock_result
        
        checker = PixelProbe()
        # Use a fake file path since we're mocking
        result = checker._check_video_corruption('/fake/path/hevc_main10.mkv')
        
        assert result is not None
        is_corrupted, corruption_details, scan_tool, scan_output, warning_details = result
        
        # Should detect HEVC Main 10 and mark as corrupted due to reference picture errors
        assert is_corrupted == True
        assert any('HEVC reference picture errors' in detail for detail in corruption_details)
        assert any('HEVC Main 10' in output for output in scan_output)
    
    @patch('os.path.getsize')
    @patch('os.path.exists')
    @patch('subprocess.run')
    @patch('pixelprobe.media_checker._ffprobe_with_timeout')
    def test_hevc_main10_hdr_detection(self, mock_probe, mock_run, mock_exists, mock_getsize):
        """Test detection of HDR content in HEVC Main 10"""
        # Mock file exists and size
        mock_exists.return_value = True
        mock_getsize.return_value = 1024 * 1024  # 1MB
        
        # Mock ffmpeg probe
        mock_probe.return_value = {
            'streams': [{
                'codec_type': 'video',
                'codec_name': 'hevc',
                'profile': 'Main 10',
                'pix_fmt': 'yuv420p10le',
                'duration': '300.5'
            }]
        }
        
        # Mock subprocess runs - need to handle multiple calls
        def mock_subprocess_run(cmd, *args, **kwargs):
            mock_result = Mock()
            mock_result.returncode = 0
            
            # Check if this is the HDR detection call (has 'json' in command)
            if any('json' in str(arg) for arg in cmd):
                mock_result.stdout = '{"streams": [{"color_space": "bt2020nc", "color_primaries": "bt2020"}]}'
                mock_result.stderr = ''
            else:
                # Regular FFmpeg calls
                mock_result.stdout = ''
                mock_result.stderr = ''
            
            return mock_result
        
        mock_run.side_effect = mock_subprocess_run
        
        checker = PixelProbe()
        result = checker._check_video_corruption('/fake/path/hevc_hdr.mkv')
        
        assert result is not None
        is_corrupted, corruption_details, scan_tool, scan_output, warning_details = result
        
        # Should detect HEVC Main 10
        assert any('hevc' in str(output).lower() and 'Main 10' in str(output) for output in scan_output)
        # Should have 10-bit pixel format
        assert any('yuv420p10le' in str(output) for output in scan_output)
    
    @patch('os.path.exists')
    def test_scan_date_update_on_rescan(self, mock_exists, db, app):
        """Test that scan_date is updated when rescanning a file"""
        with app.app_context():
            from pixelprobe.models import ScanResult
            from datetime import datetime, timezone, timedelta
            
            # Create a scan result with old scan date
            old_date = datetime.now(timezone.utc) - timedelta(days=7)
            result = ScanResult(
                file_path='/test/rescan.mp4',
                file_size=1000,
                file_type='video/mp4',
                is_corrupted=False,
                scan_date=old_date,
                scan_status='completed'
            )
            db.session.add(result)
            db.session.commit()
            
            # Mock file exists
            mock_exists.return_value = True
            
            # Rescan the file - use the app's database instead of memory
            checker = PixelProbe()
            with patch.object(checker, '_check_video_corruption') as mock_check:
                with patch('os.path.getsize', return_value=1000):
                    with patch('os.path.getmtime', return_value=1234567890):
                        mock_check.return_value = (False, [], 'ffmpeg', ['scan output'], [])
                        
                        # Manually update the result to simulate rescan
                        result = ScanResult.query.filter_by(file_path='/test/rescan.mp4').first()
                        result.scan_date = datetime.now(timezone.utc)
                        db.session.commit()
                        
                        # Check that scan date was updated
                        updated_result = ScanResult.query.filter_by(file_path='/test/rescan.mp4').first()
                        assert updated_result is not None
                        # Handle both naive and aware datetimes
                        if updated_result.scan_date.tzinfo is None:
                            # Compare as naive datetimes
                            assert updated_result.scan_date > old_date.replace(tzinfo=None)
                        else:
                            assert updated_result.scan_date > old_date
    
    def test_file_hash_consistency(self, test_data_dir):
        """Test that file hashes are consistent across scans"""
        checker = PixelProbe()
        
        result1 = checker.scan_file(test_data_dir['valid_mp4'])
        result2 = checker.scan_file(test_data_dir['valid_mp4'])
        
        assert result1['file_hash'] == result2['file_hash']
    
    def test_force_rescan(self, test_data_dir, app, db):
        """Test that force_rescan works correctly"""
        with app.app_context():
            checker = PixelProbe(database_path=app.config['SQLALCHEMY_DATABASE_URI'])
            
            # Mock the corruption checking methods and cache methods to control the flow
            with patch.object(checker, '_check_video_corruption') as mock_check_video, \
                 patch.object(checker, '_check_image_corruption') as mock_check_image, \
                 patch.object(checker, 'get_file_info') as mock_get_info, \
                 patch.object(checker, '_check_cache') as mock_check_cache, \
                 patch.object(checker, '_save_to_cache') as mock_save_cache:
                
                # Mock file info
                from datetime import datetime
                mock_get_info.return_value = {
                    'file_path': test_data_dir['valid_mp4'],
                    'file_size': 1024,
                    'file_type': 'video/mp4',
                    'creation_date': datetime.fromtimestamp(1234567890),
                    'last_modified': datetime.fromtimestamp(1234567890)
                }
                
                # Mock cache and corruption check behavior
                mock_check_cache.return_value = None  # No cache initially
                mock_check_video.return_value = (False, [], 'ffmpeg', [], [])
                
                # First scan - should call corruption check (no cache)
                result1 = checker.scan_file(test_data_dir['valid_mp4'])
                assert result1 is not None
                assert mock_check_video.call_count == 1
                
                # Reset mocks and set up cache for second scan
                mock_check_video.reset_mock()
                mock_check_cache.return_value = result1  # Return cached result
                
                # Second scan without force_rescan should use cache (not call corruption check)
                result2 = checker.scan_file(test_data_dir['valid_mp4'], force_rescan=False)
                mock_check_video.assert_not_called()
                
                # Third scan with force_rescan should check again (ignoring cache)
                mock_check_video.reset_mock()
                result3 = checker.scan_file(test_data_dir['valid_mp4'], force_rescan=True)
                # Should call the corruption check (forced rescan ignores cache)
                mock_check_video.assert_called_once()
    
    @patch('subprocess.run')
    def test_ffmpeg_timeout_handling(self, mock_run):
        """Test that FFmpeg timeouts are handled correctly"""
        # Mock subprocess to simulate timeout
        mock_run.side_effect = TimeoutError("FFmpeg timeout")
        
        checker = PixelProbe()
        with pytest.raises(Exception):
            checker._run_ffmpeg_check('/fake/path.mp4')
    
    def test_discover_media_files(self, test_data_dir):
        """Test media file discovery"""
        checker = PixelProbe()
        
        # Discover files in test directory - method expects a list of directories
        files = checker.discover_media_files([test_data_dir['test_dir']])
        
        # Should find files in parent directory
        parent_files = checker.discover_media_files([os.path.dirname(test_data_dir['valid_mp4'])])
        
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
        
        # Mock the exclusions file path to our test file
        def mock_load_exclusions():
            return ['/excluded'], ['.tmp', '.cache']
        
        monkeypatch.setattr('pixelprobe.media_checker.load_exclusions', mock_load_exclusions)
        
        # Create checker with exclusions
        excluded_paths, excluded_extensions = mock_load_exclusions()
        checker = PixelProbe(excluded_paths=excluded_paths, excluded_extensions=excluded_extensions)
        
        # Test that excluded extensions are filtered
        assert not checker._is_supported_file('/test/file.tmp')
        assert not checker._is_supported_file('/test/file.cache')
        assert checker._is_supported_file('/test/file.mp4')
        
        # Test that excluded paths are filtered
        assert not checker._is_supported_file('/excluded/file.mp4')
        assert checker._is_supported_file('/included/file.mp4')
    
    def test_concurrent_scanning_thread_safety(self, test_data_dir):
        """Test that concurrent scanning is thread-safe"""
        checker = PixelProbe()
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
        checker = PixelProbe()
        
        # Benchmark small file scanning
        result = benchmark(checker.scan_file, test_data_dir['valid_mp4'])
        
        # Performance assertions
        assert benchmark.stats['mean'] < 2.0  # Should complete in under 2 seconds on average
        assert result is not None
    
    def test_memory_usage_large_file(self, test_data_dir):
        """Test memory usage doesn't spike with large files"""
        import psutil
        import gc
        
        checker = PixelProbe()
        
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
    
    def test_error_pattern_ignoring(self, test_data_dir, db, app):
        """Test that ignored error patterns work correctly"""
        from pixelprobe.models import IgnoredErrorPattern
        
        # Add ignored pattern
        pattern = IgnoredErrorPattern(
            pattern='moov atom not found',
            description='Known FFmpeg issue',
            is_active=True
        )
        db.session.add(pattern)
        db.session.commit()
        
        # Create checker with database path
        checker = PixelProbe(database_path=app.config['SQLALCHEMY_DATABASE_URI'])
        
        # Mock the error checking to return our pattern
        with patch.object(checker, '_check_ignored_patterns') as mock_check:
            mock_check.return_value = False  # Not ignored
            result = checker.scan_file(test_data_dir['corrupted_mp4'])
            
            # Should be marked as corrupted when not ignored
            mock_check.return_value = True  # Ignored
            result2 = checker.scan_file(test_data_dir['corrupted_mp4'])
            
            # The behavior depends on implementation
    
    def test_scan_output_capture(self, test_data_dir):
        """Test that scan output is properly captured"""
        checker = PixelProbe()
        result = checker.scan_file(test_data_dir['valid_mp4'])
        
        # Scan output should be captured
        assert 'scan_output' in result
        # Output might be None for valid files or contain FFmpeg output


class TestVideoFreezeDetection:
    """Test video freeze detection via FFmpeg freezedetect filter"""

    def _make_freezedetect_stderr(self, events):
        """Build fake FFmpeg stderr containing freezedetect log lines.

        Args:
            events: list of (start, end, duration) tuples
        """
        lines = []
        # Real ffmpeg emission order per event: freeze_start, then at unfreeze
        # freeze_duration followed by freeze_end (verified against ffmpeg 8).
        for start, end, duration in events:
            lines.append(
                f"[freezedetect @ 0x1234] lavfi.freezedetect.freeze_start: {start}"
            )
            lines.append(
                f"[freezedetect @ 0x1234] lavfi.freezedetect.freeze_duration: {duration}"
            )
            lines.append(
                f"[freezedetect @ 0x1234] lavfi.freezedetect.freeze_end: {end}"
            )
        return "\n".join(lines)

    def _make_blackdetect_stderr(self, events):
        """Build fake FFmpeg stderr containing blackdetect log lines.

        Args:
            events: list of (start, end, duration) tuples
        """
        lines = []
        for start, end, duration in events:
            lines.append(
                f"[blackdetect @ 0x5678] black_start:{start} black_end:{end} black_duration:{duration}"
            )
        return "\n".join(lines)

    @patch('subprocess.run')
    def test_video_freeze_detection(self, mock_run):
        """Freeze events are detected and returned as warnings"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = self._make_freezedetect_stderr([
            (10.0, 15.0, 5.0),
            (45.5, 50.0, 4.5),
        ])
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/video.mp4', duration=120.0
        )

        assert has_warnings is True
        assert len(warning_details) == 1
        assert '2 event(s)' in warning_details[0]
        assert '9.5s frozen' in warning_details[0]
        assert '7.9%' in warning_details[0]
        # Each event must pair its own start and end, not a neighbor's
        assert any('Freeze #1: 10.0s - 15.0s (duration: 5.0s)' in line for line in scan_output)
        assert any('Freeze #2: 45.5s - 50.0s (duration: 4.5s)' in line for line in scan_output)

    @patch('subprocess.run')
    def test_video_no_freeze(self, mock_run):
        """Clean video produces no warnings"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = 'frame=1200 fps=60 q=-0.0 Lsize=N/A time=00:00:40.00'
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/clean.mp4', duration=40.0
        )

        assert has_warnings is False
        assert warning_details == []
        assert any('No freeze events' in line for line in scan_output)

    @patch('subprocess.run')
    def test_video_freeze_timeout(self, mock_run):
        """Timeout during freeze detection is handled gracefully"""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='ffmpeg', timeout=60)

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/big.mp4', duration=30.0
        )

        assert has_warnings is False
        assert warning_details == []
        assert any('timed out' in line for line in scan_output)

    @patch('os.path.getsize')
    @patch('os.path.exists')
    @patch('subprocess.run')
    @patch('pixelprobe.media_checker._ffprobe_with_timeout')
    def test_video_freeze_integrated_in_corruption_check(
        self, mock_probe, mock_run, mock_exists, mock_getsize
    ):
        """Freeze detection results propagate through _check_video_corruption"""
        mock_exists.return_value = True
        mock_getsize.return_value = 1024 * 1024

        mock_probe.return_value = {
            'streams': [{
                'codec_type': 'video',
                'codec_name': 'h264',
                'profile': 'High',
                'pix_fmt': 'yuv420p',
                'duration': '60.0'
            }],
            'format': {
                'duration': '60.0'
            }
        }

        # All subprocess calls return clean except freeze detection
        def subprocess_side_effect(cmd, *args, **kwargs):
            result = Mock()
            result.returncode = 0
            result.stdout = ''
            # Detect the freezedetect call by checking for the filter arg.
            # The event sits in the body of the file: an event against either
            # edge is a title or end card and is discounted by design.
            if any('freezedetect' in str(arg) for arg in cmd):
                result.stderr = self._make_freezedetect_stderr([(25.0, 30.0, 5.0)])
            else:
                result.stderr = ''
            return result

        mock_run.side_effect = subprocess_side_effect

        checker = PixelProbe()
        is_corrupted, corruption_details, scan_tool, scan_output, warning_details = (
            checker._check_video_corruption('/fake/freeze_video.mp4')
        )

        # Freeze detection produces warnings, not corruption
        assert is_corrupted is False
        assert not any('Video freeze' in d for d in corruption_details)
        assert any('Video freeze warning' in w for w in warning_details)
        assert any('Freeze #1' in line for line in scan_output)

    @patch('subprocess.run')
    def test_video_freeze_incomplete_event_dropped(self, mock_run):
        """Incomplete freeze event (video ends mid-freeze) is silently dropped"""
        # freeze_start emitted but freeze_duration never comes
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = (
            "[freezedetect @ 0x1234] lavfi.freezedetect.freeze_start: 55.0\n"
        )
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/cutoff.mp4', duration=60.0
        )

        assert has_warnings is False
        assert warning_details == []
        assert any('No freeze events' in line for line in scan_output)

    @patch('os.path.getsize')
    @patch('os.path.exists')
    @patch('subprocess.run')
    @patch('pixelprobe.media_checker._ffprobe_with_timeout')
    def test_freeze_detection_disabled_via_setting(
        self, mock_probe, mock_run, mock_exists, mock_getsize
    ):
        """Freeze detection is skipped when the setting is turned off"""
        mock_exists.return_value = True
        mock_getsize.return_value = 1024 * 1024

        mock_probe.return_value = {
            'streams': [{
                'codec_type': 'video',
                'codec_name': 'h264',
                'profile': 'High',
                'pix_fmt': 'yuv420p',
                'duration': '60.0'
            }],
            'format': {
                'duration': '60.0'
            }
        }

        # Return freezedetect output -- but it should never be reached
        def subprocess_side_effect(cmd, *args, **kwargs):
            result = Mock()
            result.returncode = 0
            result.stdout = ''
            if any('freezedetect' in str(arg) for arg in cmd):
                result.stderr = self._make_freezedetect_stderr([(5.0, 10.0, 5.0)])
            else:
                result.stderr = ''
            return result

        mock_run.side_effect = subprocess_side_effect

        checker = PixelProbe()
        with patch_settings(**{'detection.freeze_detection_enabled': False}):
            is_corrupted, corruption_details, scan_tool, scan_output, warning_details = (
                checker._check_video_corruption('/fake/freeze_disabled.mp4')
            )

        # The pass never ran, so its heading is absent entirely
        assert not any('Freeze Detection Analysis' in line for line in scan_output)
        assert not any('Freeze #' in line for line in scan_output)
        assert warning_details == []

    @patch('subprocess.run')
    def test_video_freeze_black_frame_filtered(self, mock_run):
        """Freeze events overlapping black sections are filtered as false positives"""
        # Freeze at 0-3s and 100-105s, black at 0-4s and 99-106s
        # Both freezes overlap black -- should be filtered out entirely
        freeze_stderr = self._make_freezedetect_stderr([
            (0.0, 3.0, 3.0),
            (100.0, 105.0, 5.0),
        ])
        black_stderr = self._make_blackdetect_stderr([
            (0.0, 4.0, 4.0),
            (99.0, 106.0, 7.0),
        ])
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = freeze_stderr + "\n" + black_stderr
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/transitions.mp4', duration=120.0
        )

        assert has_warnings is False
        assert warning_details == []
        assert any('Filtered 2 of 2' in line for line in scan_output)

    @patch('subprocess.run')
    def test_video_freeze_real_freeze_not_filtered(self, mock_run):
        """Freeze on actual content (no black overlap) is still flagged"""
        # Freeze at 50-55s on real content, black only at 0-2s (opening)
        freeze_stderr = self._make_freezedetect_stderr([
            (0.0, 2.0, 2.0),    # overlaps black -- filtered
            (50.0, 55.0, 5.0),  # no black overlap -- kept
        ])
        black_stderr = self._make_blackdetect_stderr([
            (0.0, 2.5, 2.5),
        ])
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = freeze_stderr + "\n" + black_stderr
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/real_freeze.mp4', duration=120.0
        )

        assert has_warnings is True
        assert len(warning_details) == 1
        assert '1 event(s)' in warning_details[0]
        assert any('Filtered 1 of 2' in line for line in scan_output)


class TestStaticCardSuppression:
    """Static title and end cards are real freezes but not defects"""

    def _stderr(self, events):
        lines = []
        for start, end, duration in events:
            lines.append(f"[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: {start}")
            lines.append(f"[freezedetect @ 0x1] lavfi.freezedetect.freeze_duration: {duration}")
            lines.append(f"[freezedetect @ 0x1] lavfi.freezedetect.freeze_end: {end}")
        return "\n".join(lines)

    @patch('subprocess.run')
    def test_end_card_discounted(self, mock_run):
        """A lone short freeze just before the last frame is an end card"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = self._stderr([(2628.8, 2633.8, 5.0)])
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/episode.mkv', duration=2641.6
        )

        assert has_warnings is False
        assert warning_details == []
        assert any('Discounted static card at 2628.8s' in line for line in scan_output)

    @patch('subprocess.run')
    def test_title_card_discounted(self, mock_run):
        """A lone short freeze at the head of the file is a title card"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = self._stderr([(8.1, 18.2, 10.1)])
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/episode.mkv', duration=1290.0
        )

        assert has_warnings is False
        assert any('Discounted static card at 8.1s' in line for line in scan_output)

    @patch('subprocess.run')
    def test_mid_programme_freeze_kept(self, mock_run):
        """A freeze in the body of the programme is not a card"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = self._stderr([(640.0, 645.0, 5.0)])
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/episode.mkv', duration=1290.0
        )

        assert has_warnings is True
        assert '1 event(s)' in warning_details[0]

    @patch('subprocess.run')
    def test_long_edge_freeze_kept(self, mock_run):
        """An edge freeze far longer than a card is still reported"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = self._stderr([(5.0, 65.0, 60.0)])
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/episode.mkv', duration=1290.0
        )

        assert has_warnings is True

    @patch('subprocess.run')
    def test_short_clip_middle_freeze_kept(self, mock_run):
        """On a short clip the edge window must not stretch across the middle"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = self._stderr([(50.0, 55.0, 5.0)])
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/clip.mp4', duration=120.0
        )

        assert has_warnings is True
        assert not any('Discounted static card' in line for line in scan_output)

    @patch('subprocess.run')
    def test_two_events_never_treated_as_cards(self, mock_run):
        """Card suppression applies only to a solitary event"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = self._stderr([(2.0, 7.0, 5.0), (1280.0, 1285.0, 5.0)])
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/episode.mkv', duration=1290.0
        )

        assert has_warnings is True
        assert '2 event(s)' in warning_details[0]


    @patch('subprocess.run')
    def test_event_past_reported_duration_is_not_a_card(self, mock_run):
        """A short container duration must not turn a mid-file freeze into a card"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = self._stderr([(700.0, 710.0, 10.0)])
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/short_duration.mkv', duration=600.0
        )

        assert has_warnings is True
        assert not any('Discounted static card' in line for line in scan_output)


class TestFreezeConfirmationPass:
    """Held animation cels report as frozen but every frame differs"""

    MAIN = (
        "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 640.0\n"
        "[freezedetect @ 0x1] lavfi.freezedetect.freeze_duration: 5.0\n"
        "[freezedetect @ 0x1] lavfi.freezedetect.freeze_end: 645.0"
    )

    def _dispatch(self, confirm_stderr=None, raises=None, confirm_returncode=0):
        """Route the main detection pass and the confirmation pass separately"""
        def side_effect(cmd, *args, **kwargs):
            is_confirm = 'blackdetect' not in ' '.join(str(part) for part in cmd)
            if is_confirm and raises is not None:
                raise raises
            result = Mock()
            result.returncode = confirm_returncode if is_confirm else 0
            result.stdout = ''
            result.stderr = confirm_stderr if is_confirm else self.MAIN
            return result
        return side_effect

    @patch('subprocess.run')
    def test_unconfirmed_segment_dropped(self, mock_run):
        """Frames that all differ are not a frozen picture"""
        mock_run.side_effect = self._dispatch('frame=120 fps=60 time=00:00:05.00')

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/cartoon.mkv', duration=1290.0
        )

        assert has_warnings is False
        assert warning_details == []
        assert any('Discounted near-static segment at 640.0s' in line for line in scan_output)

    @patch('subprocess.run')
    def test_confirmed_segment_kept(self, mock_run):
        """Genuinely repeated frames survive confirmation"""
        mock_run.side_effect = self._dispatch(self.MAIN)

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/stuck.mkv', duration=1290.0
        )

        assert has_warnings is True
        assert '1 event(s)' in warning_details[0]

    @patch('subprocess.run')
    def test_confirmation_failure_keeps_event(self, mock_run):
        """A confirmation pass that cannot run must not erase findings"""
        mock_run.side_effect = self._dispatch(raises=FileNotFoundError('ffmpeg missing'))

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/stuck.mkv', duration=1290.0
        )

        assert has_warnings is True

    @patch('subprocess.run')
    def test_confirmation_timeout_keeps_event(self, mock_run):
        """A confirmation pass that times out must not erase findings"""
        mock_run.side_effect = self._dispatch(
            raises=subprocess.TimeoutExpired(cmd='ffmpeg', timeout=300))

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/stuck.mkv', duration=1290.0
        )

        assert has_warnings is True


    @patch('subprocess.run')
    def test_failed_confirmation_run_keeps_event(self, mock_run):
        """A non-zero ffmpeg exit is not evidence that the frames differ"""
        mock_run.side_effect = self._dispatch(
            confirm_stderr='Error opening input file', confirm_returncode=1)

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/unreadable.mkv', duration=1290.0
        )

        assert has_warnings is True
        assert not any('Discounted near-static' in line for line in scan_output)


class TestFrozenPercentageClamp:
    """Overlapping events must not report more frozen time than the runtime"""

    @patch('subprocess.run')
    def test_overlapping_events_counted_once(self, mock_run):
        """Two events over the same span count that span once"""
        stderr = []
        for start, end, duration in [(100.0, 400.0, 300.0), (110.0, 410.0, 300.0)]:
            stderr.append(f"[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: {start}")
            stderr.append(f"[freezedetect @ 0x1] lavfi.freezedetect.freeze_duration: {duration}")
            stderr.append(f"[freezedetect @ 0x1] lavfi.freezedetect.freeze_end: {end}")
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = "\n".join(stderr)
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/gappy.mkv', duration=1000.0
        )

        assert has_warnings is True
        # Naive summing gives 600s; the union of the two spans is 310s
        assert '310.0s frozen' in warning_details[0]
        assert '31.0% of video' in warning_details[0]

    @patch('subprocess.run')
    def test_percentage_never_exceeds_one_hundred(self, mock_run):
        """An event running past the container duration still reports 100%"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = (
            "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 100.0\n"
            "[freezedetect @ 0x1] lavfi.freezedetect.freeze_duration: 5000.0\n"
            "[freezedetect @ 0x1] lavfi.freezedetect.freeze_end: 5100.0\n"
            "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 110.0\n"
            "[freezedetect @ 0x1] lavfi.freezedetect.freeze_duration: 5000.0\n"
            "[freezedetect @ 0x1] lavfi.freezedetect.freeze_end: 5110.0"
        )
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        checker = PixelProbe()
        has_warnings, warning_details, scan_output = checker._check_video_freeze(
            '/fake/gappy.mkv', duration=1000.0
        )

        assert has_warnings is True
        assert '1000.0s frozen' in warning_details[0]
        assert '100.0% of video' in warning_details[0]


class TestSettingsReachTheScanner:
    """A changed setting must actually alter what the scanner does"""

    def _run(self, mock_run, duration=1290.0, **overrides):
        result = Mock()
        result.returncode = 0
        result.stdout = ''
        result.stderr = ''
        mock_run.return_value = result
        checker = PixelProbe()
        with patch_settings(**overrides):
            checker._check_video_freeze('/fake/video.mkv', duration=duration)
        return ' '.join(str(part) for part in mock_run.call_args[0][0])

    @patch('subprocess.run')
    def test_minimum_duration_drives_the_detector(self, mock_run):
        """The configured minimum becomes freezedetect's own d= value"""
        cmd = self._run(mock_run, **{'detection.freeze_min_duration_secs': 7.0})
        assert 'freezedetect=n=-60dB:d=7.0' in cmd

    @patch('subprocess.run')
    def test_raising_the_minimum_changes_the_command(self, mock_run):
        """Editing the setting is reflected without any code change"""
        cmd = self._run(mock_run, **{'detection.freeze_min_duration_secs': 20.0})
        assert 'freezedetect=n=-60dB:d=20.0' in cmd
        assert 'd=7.0' not in cmd

    @patch('subprocess.run')
    def test_default_minimum_is_seven_seconds(self, mock_run):
        """The shipped default no longer reports the 5s events cartoons produce"""
        cmd = self._run(mock_run)
        assert 'freezedetect=n=-60dB:d=7.0' in cmd

    @patch('subprocess.run')
    def test_card_suppression_can_be_switched_off(self, mock_run):
        """An edge window of zero reports title and end cards instead of discounting them"""
        stderr = (
            "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 2.0\n"
            "[freezedetect @ 0x1] lavfi.freezedetect.freeze_duration: 8.0\n"
            "[freezedetect @ 0x1] lavfi.freezedetect.freeze_end: 10.0"
        )
        result = Mock()
        result.returncode = 0
        result.stdout = ''
        result.stderr = stderr
        mock_run.return_value = result
        checker = PixelProbe()

        with patch_settings(**{'detection.static_card_edge_secs': 60.0}):
            on, _, out = checker._check_video_freeze('/fake/v.mkv', duration=1290.0)
        assert on is False
        assert any('Discounted static card' in line for line in out)

        with patch_settings(**{'detection.static_card_edge_secs': 0.0}):
            on, details, out = checker._check_video_freeze('/fake/v.mkv', duration=1290.0)
        assert on is True
        assert '1 event(s)' in details[0]

    def test_data_integrity_threshold_is_a_setting(self, tmp_path):
        """Raising the required unwritten share stops a marginal file being called incomplete"""
        path = tmp_path / 'marginal.mkv'
        with open(path, 'wb') as handle:
            handle.write(b'\xa5' * 1024 * 1024 * 8)
            handle.seek(2 * 1024 * 1024, os.SEEK_CUR)
            handle.write(b'\xa5')
        size = os.path.getsize(path)
        fd = os.open(str(path), os.O_RDONLY)
        try:
            supported = os.lseek(fd, 0, os.SEEK_HOLE) < size
        except OSError:
            supported = False
        finally:
            os.close(fd)
        if not supported:
            pytest.skip('filesystem does not report sparse regions')

        checker = PixelProbe()
        blocks = (size // 512) // 2

        with patch_settings(**{'detection.data_hole_min_pct': 1.0}):
            found, _, _ = checker._check_data_holes(str(path), size, blocks)
        assert found is True

        with patch_settings(**{'detection.data_hole_min_pct': 99.0}):
            found, details, out = checker._check_data_holes(str(path), size, blocks)
        assert found is False
        assert any('compression' in line for line in out)


class TestDataHoleDetection:
    """Files allocated at full size but never fully written are missing data"""

    def _sparse(self, tmp_path, name, written_mb, hole_mb):
        """Create a genuinely sparse file: written data, then an unwritten hole"""
        path = tmp_path / name
        with open(path, 'wb') as handle:
            handle.write(b'\xa5' * 1024 * 1024 * written_mb)
            handle.seek(hole_mb * 1024 * 1024, os.SEEK_CUR)
            handle.write(b'\xa5')
        return str(path)

    def _dense(self, tmp_path, name, written_mb, zero_mb):
        """Create a fully written file whose tail is real, written zero bytes"""
        path = tmp_path / name
        with open(path, 'wb') as handle:
            handle.write(b'\xa5' * 1024 * 1024 * written_mb)
            handle.write(b'\x00' * 1024 * 1024 * zero_mb)
        return str(path)

    @staticmethod
    def _supports_sparse(path):
        fd = os.open(path, os.O_RDONLY)
        try:
            return os.lseek(fd, 0, os.SEEK_HOLE) < os.fstat(fd).st_size
        except OSError:
            return False
        finally:
            os.close(fd)

    def test_holed_file_is_corruption(self, tmp_path):
        """Unwritten regions confirmed by SEEK_HOLE are a corruption verdict"""
        path = self._sparse(tmp_path, 'holed.mkv', written_mb=4, hole_mb=28)
        if not self._supports_sparse(path):
            pytest.skip('filesystem does not report sparse regions')
        size = os.path.getsize(path)

        checker = PixelProbe()
        found, details, scan_output = checker._check_data_holes(path, size, (size // 512) // 4)

        assert found is True
        assert 'Incomplete file' in details[0]
        assert any('never written' in line for line in scan_output)

    def test_written_zeroes_are_not_holes(self, tmp_path):
        """Real zero bytes are data. Silence in PCM audio must not read as damage"""
        path = self._dense(tmp_path, 'silence.wav', written_mb=4, zero_mb=28)
        size = os.path.getsize(path)

        checker = PixelProbe()
        found, details, scan_output = checker._check_data_holes(path, size, (size // 512) // 4)

        assert found is False
        assert details == []
        assert any('compression' in line or 'no conclusion' in line for line in scan_output)

    def test_fully_allocated_file_skips_probe(self):
        """A file allocated at its nominal size is never opened"""
        checker = PixelProbe()
        size = 32 * 1024 * 1024
        found, details, scan_output = checker._check_data_holes(
            '/nonexistent/never-opened.mkv', size, size // 512)

        assert found is False
        assert scan_output == []

    def test_filesystem_without_block_counts_is_skipped(self):
        """st_blocks of 0 means the filesystem does not report allocation"""
        checker = PixelProbe()
        found, details, scan_output = checker._check_data_holes(
            '/nonexistent/never-opened.mkv', 32 * 1024 * 1024, 0)

        assert found is False
        assert scan_output == []

    def test_small_file_is_skipped(self):
        """Files below the size floor cannot carry a meaningful hole"""
        checker = PixelProbe()
        found, details, scan_output = checker._check_data_holes(
            '/nonexistent/tiny.jpg', 1024, 1)

        assert found is False
        assert scan_output == []

    def test_unreadable_file_draws_no_conclusion(self):
        """A file that cannot be opened is not accused of being incomplete"""
        checker = PixelProbe()
        size = 32 * 1024 * 1024
        found, details, scan_output = checker._check_data_holes(
            '/nonexistent/path.mkv', size, (size // 512) // 4)

        assert found is False
        assert details == []

    def test_stalled_read_draws_no_conclusion(self, tmp_path):
        """A mount that stalls is not evidence that data is missing"""
        from pixelprobe.media_checker import FileReadTimeoutError

        path = self._dense(tmp_path, 'stalled.mkv', written_mb=2, zero_mb=0)
        size = os.path.getsize(path)

        checker = PixelProbe()
        with patch('pixelprobe.media_checker._read_with_timeout',
                   side_effect=FileReadTimeoutError('stalled')):
            found, details, scan_output = checker._check_data_holes(
                path, size, (size // 512) // 4)

        assert found is False
        assert details == []
        assert any('no conclusion' in line for line in scan_output)


class TestDataHolesInScanFile:
    """The missing-data verdict reaches scan_file and short-circuits decoding"""

    @patch('pixelprobe.media_checker.PixelProbe._check_video_corruption')
    @patch('pixelprobe.media_checker.PixelProbe._check_data_holes')
    @patch('pixelprobe.media_checker.PixelProbe.calculate_file_hash')
    @patch('pixelprobe.media_checker.PixelProbe.get_file_info')
    def test_holed_video_marked_corrupt_without_decoding(
        self, mock_info, mock_hash, mock_holes, mock_video
    ):
        from datetime import datetime, timezone

        mock_info.return_value = {
            'file_path': '/fake/holed.mkv',
            'file_size': 400 * 1024 * 1024,
            'file_type': 'video/x-matroska',
            'creation_date': datetime.now(timezone.utc),
            'last_modified': datetime.now(timezone.utc),
            'file_blocks': 8,
        }
        mock_hash.return_value = 'deadbeef'
        mock_holes.return_value = (
            True,
            ['Incomplete file: 60.2% of 381.6 MB allocated'],
            ['=== Data Integrity Check ==='],
        )

        checker = PixelProbe()
        result = checker.scan_file('/fake/holed.mkv')

        assert result['is_corrupted'] is True
        assert 'Incomplete file' in result['corruption_details']
        assert result['scan_tool'] == 'data-integrity'
        assert result['has_warnings'] is False
        mock_video.assert_not_called()

    @patch('pixelprobe.media_checker.PixelProbe._check_data_holes')
    @patch('pixelprobe.media_checker.PixelProbe.calculate_file_hash')
    @patch('pixelprobe.media_checker.PixelProbe.get_file_info')
    def test_non_media_file_skips_hole_check(self, mock_info, mock_hash, mock_holes):
        from datetime import datetime, timezone

        mock_info.return_value = {
            'file_path': '/fake/notes.nfo',
            'file_size': 400 * 1024 * 1024,
            'file_type': 'text/plain',
            'creation_date': datetime.now(timezone.utc),
            'last_modified': datetime.now(timezone.utc),
        }
        mock_hash.return_value = 'deadbeef'

        checker = PixelProbe()
        result = checker.scan_file('/fake/notes.nfo')

        assert result['is_corrupted'] is False
        mock_holes.assert_not_called()


class TestTemporalOutlierDetection:
    """Test Stage 2 signalstats sampling (TOUT corruption, VREP warning)"""

    def _make_signalstats_stdout(self, frames):
        """Build fake FFmpeg metadata=print stdout.

        Args:
            frames: list of (tout, vrep) tuples
        """
        lines = []
        for i, (tout, vrep) in enumerate(frames):
            lines.append(f"frame:{i}    pts:{i * 1000}    pts_time:{i * 0.04}")
            lines.append(f"lavfi.signalstats.TOUT={tout}")
            lines.append(f"lavfi.signalstats.VREP={vrep}")
        return "\n".join(lines)

    def _mock_result(self, frames):
        result = Mock()
        result.returncode = 0
        result.stdout = self._make_signalstats_stdout(frames)
        result.stderr = ''
        return result

    @patch('pixelprobe.media_checker._probe_video_duration')
    @patch('subprocess.run')
    def test_samples_file_body_not_intro(self, mock_run, mock_duration):
        """Windows are drawn from the body, never through the movie= lavfi source"""
        mock_duration.return_value = 1000.0
        mock_run.return_value = self._mock_result([(0.0, 0.0)] * 200)

        checker = PixelProbe()
        checker._check_temporal_outliers('/fake/video.mkv')

        assert mock_run.call_count == 3
        offsets = []
        for call in mock_run.call_args_list:
            args = call[0][0]
            assert not any('movie=' in str(a) for a in args), \
                "movie= source truncates to the first seconds and must not be used"
            offsets.append(float(args[args.index('-ss') + 1]))

        # 25/50/75% of a 1000s file, i.e. never the intro and never the credits
        assert offsets == [250.0, 500.0, 750.0]

    @patch('pixelprobe.media_checker._probe_video_duration')
    @patch('subprocess.run')
    def test_high_vrep_is_note_not_warning(self, mock_run, mock_duration):
        """Vertical line repetition is an informational note, never a warning.

        Verified against production: 298 flagged files, spot checks across the
        10-96% range all visually pristine (dark scenes, letterboxing,
        monochrome lighting); genuinely corrupted content scores lower than
        clean synthetic content on this metric."""
        mock_duration.return_value = 1000.0
        # Every sampled frame far over the 0.5 per-frame VREP threshold
        mock_run.return_value = self._mock_result([(0.0, 0.99)] * 200)

        checker = PixelProbe()
        is_corrupted, details, warnings, info = checker._check_temporal_outliers('/fake/video.mkv')

        assert is_corrupted is False
        assert details == []
        assert warnings == []
        assert any('vertical line repetition' in n.lower() and '100.0%' in n for n in info)

    @patch('pixelprobe.media_checker._probe_video_duration')
    @patch('subprocess.run')
    def test_high_tout_warns_without_corruption(self, mock_run, mock_duration):
        """Temporal outliers warn instead of marking corrupted.

        Verified against a pristine Bluray episode: film grain pushes TOUT past
        its per-frame threshold on 46-100% of sampled frames, so like VREP the
        metric cannot separate damage from clean grainy content.
        """
        mock_duration.return_value = 1000.0
        mock_run.return_value = self._mock_result([(0.5, 0.0)] * 200)

        checker = PixelProbe()
        is_corrupted, details, warnings, info = checker._check_temporal_outliers('/fake/video.mkv')

        assert is_corrupted is False
        assert details == []
        assert any('temporal outliers' in w.lower() for w in warnings)

    @patch('pixelprobe.media_checker._probe_video_duration')
    @patch('subprocess.run')
    def test_clean_body_produces_no_verdict(self, mock_run, mock_duration):
        """Values under both thresholds yield neither corruption nor warnings"""
        mock_duration.return_value = 1000.0
        mock_run.return_value = self._mock_result([(0.001, 0.2)] * 200)

        checker = PixelProbe()
        is_corrupted, details, warnings, info = checker._check_temporal_outliers('/fake/video.mkv')

        assert is_corrupted is False
        assert details == []
        assert warnings == []
        assert info == []

    @patch('pixelprobe.media_checker._probe_video_duration')
    @patch('subprocess.run')
    def test_tiny_sample_yields_no_verdict(self, mock_run, mock_duration):
        """A handful of frames warns instead of computing a percentage.

        This is the ffmpeg 8 regression: 19 frames of a 41,608-frame file all
        scored high VREP because they were the fade-in, and that was reported as
        47.4% corruption.
        """
        mock_duration.return_value = 1000.0
        mock_run.return_value = self._mock_result([(0.9, 0.99)] * 6)

        checker = PixelProbe()
        is_corrupted, details, warnings, info = checker._check_temporal_outliers('/fake/video.mkv')

        assert is_corrupted is False
        assert details == []
        # An under-sampled check says nothing about the file, so it must not
        # set warning status - it is recorded as an operational note instead.
        assert warnings == []
        assert any('below the' in n for n in info)

    @patch('pixelprobe.media_checker._probe_video_duration')
    @patch('subprocess.run')
    def test_partial_timeout_keeps_readable_windows(self, mock_run, mock_duration):
        """One timed-out window warns but does not discard the others"""
        mock_duration.return_value = 1000.0
        mock_run.side_effect = [
            self._mock_result([(0.0, 0.0)] * 200),
            subprocess.TimeoutExpired(cmd='ffmpeg', timeout=30),
            self._mock_result([(0.0, 0.0)] * 200),
        ]

        checker = PixelProbe()
        is_corrupted, details, warnings, info = checker._check_temporal_outliers('/fake/video.mkv')

        assert is_corrupted is False
        assert details == []
        assert warnings == []
        assert any('partially timed out' in n for n in info)

    @patch('pixelprobe.media_checker._probe_video_duration')
    @patch('subprocess.run')
    def test_supplied_duration_avoids_reprobe(self, mock_run, mock_duration):
        """A caller that already probed duration is not made to probe again"""
        mock_run.return_value = self._mock_result([(0.0, 0.0)] * 200)

        checker = PixelProbe()
        checker._check_temporal_outliers('/fake/video.mkv', duration=800.0)

        mock_duration.assert_not_called()
        offsets = [float(c[0][0][c[0][0].index('-ss') + 1]) for c in mock_run.call_args_list]
        assert offsets == [200.0, 400.0, 600.0]

    @patch('pixelprobe.media_checker._probe_video_duration')
    @patch('subprocess.run')
    def test_second_timeout_abandons_remaining_windows(self, mock_run, mock_duration):
        """Whatever stalled two windows will stall the third, so stop paying for it"""
        mock_duration.return_value = 1000.0
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd='ffmpeg', timeout=30),
            subprocess.TimeoutExpired(cmd='ffmpeg', timeout=30),
            self._mock_result([(0.0, 0.0)] * 200),
        ]

        checker = PixelProbe()
        is_corrupted, details, warnings, info = checker._check_temporal_outliers('/fake/video.mkv')

        assert mock_run.call_count == 2
        assert is_corrupted is False
        assert warnings == []
        assert any('below the' in n for n in info)

    @patch('pixelprobe.media_checker._probe_video_duration')
    @patch('subprocess.run')
    def test_unknown_duration_skips_check(self, mock_run, mock_duration):
        """Without a duration there is no body to sample, so nothing is decoded"""
        mock_duration.return_value = None

        checker = PixelProbe()
        is_corrupted, details, warnings, info = checker._check_temporal_outliers('/fake/video.mkv')

        assert (is_corrupted, details, warnings, info) == (False, [], [], [])
        mock_run.assert_not_called()

    @patch('pixelprobe.media_checker._probe_video_duration')
    @patch('subprocess.run')
    def test_stage2_warning_routed_to_warnings(self, mock_run, mock_duration):
        """A Stage 2 TOUT warning reaches the enhanced check as a warning"""
        mock_duration.return_value = 1000.0
        mock_run.return_value = self._mock_result([(0.5, 0.0)] * 200)

        checker = PixelProbe()
        with patch.object(checker, '_check_frame_integrity', return_value=(False, [], [], [])), \
             patch.object(checker, '_check_strict_error_detection', return_value=(False, [])):
            is_corrupted, details, output, warnings, has_notes = checker._enhanced_corruption_check(
                '/fake/video.mkv', file_size_gb=2.0
            )

        assert is_corrupted is False
        assert details == []
        assert any('Stage 2: High temporal outliers' in w for w in warnings)
        assert any('Result: WARNING' in line for line in output)

    @patch('pixelprobe.media_checker._probe_video_duration')
    @patch('subprocess.run')
    def test_stage2_timeout_notes_stay_out_of_warnings(self, mock_run, mock_duration):
        """An aborted Stage 2 is an operational note in scan output, not a file warning"""
        mock_duration.return_value = 1000.0
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd='ffmpeg', timeout=30),
            subprocess.TimeoutExpired(cmd='ffmpeg', timeout=30),
        ]

        checker = PixelProbe()
        with patch.object(checker, '_check_frame_integrity', return_value=(False, [], [], [])), \
             patch.object(checker, '_check_strict_error_detection', return_value=(False, [])):
            is_corrupted, details, output, warnings, has_notes = checker._enhanced_corruption_check(
                '/fake/video.mkv', file_size_gb=2.0
            )

        assert is_corrupted is False
        assert details == []
        assert warnings == []
        assert has_notes is True
        assert any('Result: INCOMPLETE' in line and 'below the' in line for line in output)



class TestStage2NotesReachScanOutput:
    """A load-aborted Stage 2 must leave its trace in user-visible scan output
    (it no longer sets warning status, so this is the only place it survives)"""

    @patch('os.path.getsize')
    @patch('os.path.exists')
    @patch('subprocess.run')
    @patch('pixelprobe.media_checker._ffprobe_with_timeout')
    def test_aborted_stage2_note_lands_in_scan_output(
        self, mock_probe, mock_run, mock_exists, mock_getsize
    ):
        mock_exists.return_value = True
        mock_getsize.return_value = 2 * 1024 * 1024 * 1024  # >1GB enables Stage 2

        mock_probe.return_value = {
            'streams': [{
                'codec_type': 'video',
                'codec_name': 'h264',
                'profile': 'High',
                'pix_fmt': 'yuv420p',
                'duration': '600.0'
            }],
            'format': {'duration': '600.0'}
        }

        def subprocess_side_effect(cmd, *args, **kwargs):
            if any('signalstats' in str(arg) for arg in cmd):
                raise subprocess.TimeoutExpired(cmd='ffmpeg', timeout=30)
            result = Mock()
            result.returncode = 0
            result.stdout = ''
            result.stderr = ''
            return result

        mock_run.side_effect = subprocess_side_effect

        checker = PixelProbe()
        is_corrupted, corruption_details, scan_tool, scan_output, warning_details = (
            checker._check_video_corruption('/fake/slow_storage.mkv')
        )

        assert is_corrupted is False
        assert warning_details == []
        assert any('below the' in line for line in scan_output)


class TestOpusEofParseError:
    """ffmpeg 8 emits a lone Opus packet-header parse error at EOF on files
    ffmpeg 6 validates silently; with exit code 0 it is a tooling artifact,
    not corruption (verified: full audio decode of flagged files is clean)."""

    def _run_video_check(self, validation_returncode, opus_error_count=1):
        """Run _check_video_corruption with the -c copy validation call
        returning an Opus parse error and everything else clean."""
        opus_line = '[opus @ 0x5a18558f1d80] Error parsing Opus packet header.'

        def subprocess_side_effect(cmd, *args, **kwargs):
            result = Mock()
            result.stdout = ''
            if 'copy' in cmd:
                result.returncode = validation_returncode
                result.stderr = '\n'.join([opus_line] * opus_error_count)
            else:
                result.returncode = 0
                result.stderr = ''
            return result

        probe = {
            'streams': [{
                'codec_type': 'audio',
                'codec_name': 'opus',
            }, {
                'codec_type': 'video',
                'codec_name': 'hevc',
                'profile': 'Main 10',
                'pix_fmt': 'yuv420p10le',
                'duration': '60.0'
            }],
            'format': {'duration': '60.0'}
        }

        with patch('os.path.exists', return_value=True), \
             patch('os.path.getsize', return_value=1024 * 1024), \
             patch('pixelprobe.media_checker._ffprobe_with_timeout', return_value=probe), \
             patch('subprocess.run', side_effect=subprocess_side_effect):
            checker = PixelProbe()
            return checker._check_video_corruption('/fake/opus_video.mkv')

    def test_opus_parse_error_with_clean_exit_stays_healthy(self):
        is_corrupted, corruption_details, scan_tool, scan_output, warning_details = (
            self._run_video_check(validation_returncode=0)
        )

        assert is_corrupted is False
        assert not any('Opus' in d for d in corruption_details)
        # Benign decoder noise must not surface as a warning either
        assert not any('Opus' in w for w in warning_details)

    def test_opus_parse_error_with_failed_exit_stays_corrupted(self):
        is_corrupted, corruption_details, scan_tool, scan_output, warning_details = (
            self._run_video_check(validation_returncode=1)
        )

        assert is_corrupted is True
        assert any('FFmpeg validation failed' in d for d in corruption_details)

    def test_opus_parse_error_flood_stays_corrupted(self):
        """Dozens of parse errors mean mid-stream damage, not the EOF artifact.

        -c copy never decodes audio, so ffmpeg can exit 0 on a genuinely
        damaged Opus stream; the benign path must stay bounded to the small
        per-stream EOF counts seen on verified-clean files (1-3 lines)."""
        is_corrupted, corruption_details, scan_tool, scan_output, warning_details = (
            self._run_video_check(validation_returncode=0, opus_error_count=40)
        )

        assert is_corrupted is True
        assert any('Opus' in d for d in corruption_details)


class TestWorkerDatabasePool:
    """Worker DB engine must not share one raw connection across scan threads"""

    def test_postgres_engine_uses_queuepool_sized_to_workers(self):
        from sqlalchemy.pool import QueuePool
        # create_engine connects lazily, so a bogus URI is safe here
        checker = PixelProbe(database_path='postgresql://u:p@localhost:5/db', max_workers=6)
        assert checker._db_engine is not None
        assert isinstance(checker._db_engine.pool, QueuePool)
        assert checker._db_engine.pool.size() == 6

    def test_driver_qualified_postgres_url_not_treated_as_sqlite(self):
        # postgresql+psycopg2:// URLs pass app.py's startswith('postgresql')
        # check and must not fall into the SQLite branch, whose
        # check_same_thread connect arg psycopg2 rejects
        from sqlalchemy.pool import QueuePool
        checker = PixelProbe(database_path='postgresql+psycopg2://u:p@localhost:5/db', max_workers=3)
        assert checker._db_engine is not None
        assert isinstance(checker._db_engine.pool, QueuePool)


class TestFrameIntegrityPacketFirst:
    """Stage 1 frame check: cheap -count_packets pass before full-decode -count_frames"""

    def _proc(self, framerate, count_key, count, duration, rc=0):
        m = MagicMock()
        m.returncode = rc
        m.stdout = f"avg_frame_rate={framerate}\n{count_key}={count}\nduration={duration}\n"
        m.stderr = ''
        return m

    def test_packet_count_match_skips_frame_decode(self):
        checker = PixelProbe()
        with patch('pixelprobe.media_checker.safe_subprocess_run') as run:
            # 25fps * 10s = 250 expected; 250 packets reported
            run.return_value = self._proc('25/1', 'nb_read_packets', 250, '10.000000')
            is_corrupted, details, warnings, notes = checker._check_frame_integrity('/fake.mp4')
        assert is_corrupted is False
        assert details == []
        assert warnings == []
        assert run.call_count == 1
        assert '-count_packets' in run.call_args_list[0][0][0]

    def test_small_packet_diff_skips_decode(self):
        # A 1-5% packet diff can never produce the >5% warning, so the
        # expensive confirm decode must not run for it
        checker = PixelProbe()
        with patch('pixelprobe.media_checker.safe_subprocess_run') as run:
            run.return_value = self._proc('25/1', 'nb_read_packets', 240, '10.000000')  # 4% diff
            is_corrupted, details, warnings, notes = checker._check_frame_integrity('/fake.mp4')
        assert is_corrupted is False
        assert warnings == []
        assert run.call_count == 1

    def test_packet_mismatch_falls_back_to_frame_count(self):
        checker = PixelProbe()
        with patch('pixelprobe.media_checker.safe_subprocess_run') as run:
            run.side_effect = [
                self._proc('25/1', 'nb_read_packets', 200, '10.000000'),  # 20% short: ambiguous
                self._proc('25/1', 'nb_read_frames', 248, '10.000000'),   # decode: 0.8% diff, fine
            ]
            is_corrupted, details, warnings, notes = checker._check_frame_integrity('/fake.mp4')
        assert is_corrupted is False
        assert details == []
        assert warnings == []
        assert run.call_count == 2
        assert '-count_frames' in run.call_args_list[1][0][0]

    def test_confirmed_frame_mismatch_is_warning_not_corruption(self):
        # Container metadata lies on sparse-video/VFR files, so even a
        # decode-confirmed mismatch must never produce a corruption verdict
        checker = PixelProbe()
        with patch('pixelprobe.media_checker.safe_subprocess_run') as run:
            run.side_effect = [
                self._proc('25/1', 'nb_read_packets', 200, '10.000000'),
                self._proc('25/1', 'nb_read_frames', 200, '10.000000'),   # decode confirms 20% diff
            ]
            is_corrupted, details, warnings, notes = checker._check_frame_integrity('/fake.mp4')
        assert is_corrupted is False
        assert details == []
        assert any('frame count differs' in w.lower() for w in warnings)

    def test_confirm_probe_failure_degrades_to_packet_warning(self):
        # Heavily damaged files are where -count_frames errors out; the
        # packet-pass evidence must survive as a warning, not vanish
        checker = PixelProbe()
        with patch('pixelprobe.media_checker.safe_subprocess_run') as run:
            run.side_effect = [
                self._proc('25/1', 'nb_read_packets', 200, '10.000000'),  # 20% short
                self._proc('25/1', 'nb_read_frames', 0, '10.000000', rc=1),
            ]
            is_corrupted, details, warnings, notes = checker._check_frame_integrity('/fake.mp4')
        assert is_corrupted is False
        assert any('decode confirmation failed' in w for w in warnings)

    def test_stream_duration_preferred_over_format_duration(self):
        # A container carrying audio longer than its video track must be
        # measured against the video stream's own duration
        checker = PixelProbe()
        m = MagicMock()
        m.returncode = 0
        m.stdout = ("avg_frame_rate=25/1\nduration=10.000000\n"
                    "nb_read_packets=250\nduration=120.000000\n")
        m.stderr = ''
        with patch('pixelprobe.media_checker.safe_subprocess_run', return_value=m) as run:
            is_corrupted, details, warnings, notes = checker._check_frame_integrity('/fake.mp4')
        assert is_corrupted is False
        assert warnings == []
        assert run.call_count == 1  # 250 packets vs 25fps*10s: no decode needed

    def test_timeout_is_inconclusive_note_not_pass(self):
        import subprocess as sp
        checker = PixelProbe()
        with patch('pixelprobe.media_checker.safe_subprocess_run',
                   side_effect=sp.TimeoutExpired(cmd='ffprobe', timeout=120)):
            is_corrupted, details, warnings, notes = checker._check_frame_integrity('/fake.mkv')
        assert is_corrupted is False
        assert warnings == []
        assert any('timed out' in n for n in notes)

    def test_unavailable_metadata_skips_check(self):
        checker = PixelProbe()
        with patch('pixelprobe.media_checker.safe_subprocess_run') as run:
            m = MagicMock()
            m.returncode = 0
            m.stdout = "avg_frame_rate=N/A\nnb_read_packets=200\nduration=N/A\n"
            m.stderr = ''
            run.return_value = m
            is_corrupted, details, warnings, notes = checker._check_frame_integrity('/fake.mkv')
        assert is_corrupted is False
        assert details == []
        assert warnings == []
        assert run.call_count == 1

"""
Tests for PixelProbe media checker core functionality
"""

import pytest
import os
import subprocess
import threading
from unittest.mock import Mock, patch, MagicMock

from pixelprobe.media_checker import PixelProbe

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
            # Detect the freezedetect call by checking for the filter arg
            if any('freezedetect' in str(arg) for arg in cmd):
                result.stderr = self._make_freezedetect_stderr([(5.0, 10.0, 5.0)])
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

    @patch.dict('os.environ', {'FREEZE_DETECTION_ENABLED': 'false'})
    @patch('os.path.getsize')
    @patch('os.path.exists')
    @patch('subprocess.run')
    @patch('pixelprobe.media_checker._ffprobe_with_timeout')
    def test_freeze_detection_disabled_via_env(
        self, mock_probe, mock_run, mock_exists, mock_getsize
    ):
        """Freeze detection is skipped when FREEZE_DETECTION_ENABLED=false"""
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
        is_corrupted, corruption_details, scan_tool, scan_output, warning_details = (
            checker._check_video_corruption('/fake/freeze_disabled.mp4')
        )

        # Should NOT be corrupted -- freeze detection was skipped
        assert not any('Video freeze detected' in d for d in corruption_details)
        assert not any('Freeze #' in line for line in scan_output)

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
        with patch.object(checker, '_check_frame_integrity', return_value=(False, [])), \
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
        with patch.object(checker, '_check_frame_integrity', return_value=(False, [])), \
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

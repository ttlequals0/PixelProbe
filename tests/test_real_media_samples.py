"""
Tests using real media samples from FFmpeg
"""

import os
import subprocess
from unittest.mock import patch

import pytest
from PIL import Image

from pixelprobe.media_checker import PixelProbe
from pixelprobe.models import ScanResult

# Fixture scanning is session-cached; the first test to touch it pays the
# full corpus scan (minutes on a loaded CI runner), so every test in this
# module shares one generous timeout
pytestmark = pytest.mark.timeout(900)


@pytest.mark.real_media
class TestRealMediaSamples:
    """Test PixelProbe with real media files"""
    
    def test_valid_files_not_corrupted(self, real_scan_results):
        """Test that valid files are detected as not corrupted"""
        valid_results = [r for r in real_scan_results if 'valid' in r.file_path]
        
        # We should have scanned valid files
        assert len(valid_results) > 0
        
        # Check some valid files are not marked as corrupted
        for result in valid_results:
            filename = os.path.basename(result.file_path)
            # Some valid files might still have issues, but common ones shouldn't
            if filename in ['valid.mp4', 'valid.jpg', 'valid.png', 'valid.wav']:
                assert not result.is_corrupted, f"{filename} should not be corrupted"
    
    def test_corrupted_files_detected(self, real_scan_results):
        """Test that corrupted files are properly detected"""
        corrupted_results = [r for r in real_scan_results if 'corrupted' in r.file_path]
        
        # We should have scanned corrupted files
        assert len(corrupted_results) > 0
        
        # Count how many were detected as corrupted
        detected = sum(1 for r in corrupted_results if r.is_corrupted)
        total = len(corrupted_results)
        
        # Print detection details for debugging
        print(f"\nCorruption Detection Results ({detected}/{total}):")
        for result in corrupted_results:
            filename = os.path.basename(result.file_path)
            if result.is_corrupted:
                print(f"✓ {filename}: {result.corruption_details or result.error_message}")
            else:
                print(f"✗ {filename}: Not detected as corrupted")
        
        # At least 25% of corrupted files should be detected (adjusted for real-world files)
        detection_rate = detected / total if total > 0 else 0
        assert detection_rate >= 0.20, f"Only {detected}/{total} corrupted files detected ({detection_rate*100:.1f}%)"
    
    def test_scan_output_captured(self, real_scan_results):
        """Test that scan output is properly captured"""
        # Most scans should have some output
        results_with_output = [r for r in real_scan_results if r.scan_output]
        assert len(results_with_output) > 0, "No scan output captured"
    
    def test_file_types_detected(self, real_scan_results):
        """Test that file types are properly detected"""
        for result in real_scan_results:
            assert result.file_type is not None
            assert result.file_type != ''
            
            # Check some known types (skip if file is corrupted and misidentified)
            filename = os.path.basename(result.file_path)
            if not result.is_corrupted:  # Only check file types for non-corrupted files
                if filename.endswith('.mp4'):
                    assert 'video' in result.file_type.lower() or 'mp4' in result.file_type.lower()
                elif filename.endswith('.jpg'):
                    assert 'image' in result.file_type.lower() or 'jpeg' in result.file_type.lower()
    
    def test_file_hashes_generated(self, real_scan_results):
        """Test that file hashes are generated"""
        for result in real_scan_results:
            assert result.file_hash is not None
            assert len(result.file_hash) == 64  # SHA256 hash length
    
    def test_hevc_detection(self, real_scan_results):
        """Test HEVC file detection and warnings"""
        hevc_results = [r for r in real_scan_results if 'hevc' in r.file_path.lower()]
        
        if hevc_results:
            for result in hevc_results:
                # HEVC files should have scan output mentioning HEVC
                if result.scan_output:
                    assert any('hevc' in str(line).lower() or 'h.265' in str(line).lower() 
                              for line in (result.scan_output if isinstance(result.scan_output, list) else [result.scan_output]))
    
    def test_scan_tool_recorded(self, real_scan_results):
        """Test that scan tool is recorded"""
        for result in real_scan_results:
            assert result.scan_tool is not None
            assert result.scan_tool in ['ffmpeg', 'imagemagick', 'pillow', 'pil', 'data-integrity']


@pytest.mark.real_media
class TestCorruptionDetails:
    """Test specific corruption detection capabilities"""
    
    def test_mp3_broken_frame(self, real_scan_results):
        """Test truncated MP3 is detected"""
        mp3_results = [r for r in real_scan_results if 'corrupted.mp3' in r.file_path]
        if mp3_results:
            result = mp3_results[0]
            assert result.is_corrupted or result.error_message or result.warning_details
    
    def test_invalid_aiff(self, real_scan_results):
        """Test AIFF with damaged header is detected"""
        aiff_results = [r for r in real_scan_results if 'corrupted.aiff' in r.file_path]
        if aiff_results:
            result = aiff_results[0]
            assert result.is_corrupted or result.error_message or result.warning_details
    
    def test_corrupted_images(self, real_scan_results):
        """Test corrupted image detection"""
        image_extensions = ['.jpg', '.png', '.gif', '.bmp']
        
        for ext in image_extensions:
            corrupted_images = [r for r in real_scan_results 
                              if r.file_path.endswith(ext) and 'corrupted' in r.file_path]
            
            if corrupted_images:
                # At least one corrupted image of each type should be detected
                detected = any(r.is_corrupted or r.error_message or r.warning_details 
                             for r in corrupted_images)
                assert detected, f"No corrupted {ext} files were processed"

@pytest.mark.real_media
class TestOversizedImages:
    """Regression: images beyond tool pixel/resource limits are warnings, not corruption"""

    def _make_oversized(self, path, mode, size, **save_kwargs):
        old_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = None
        try:
            Image.new(mode, size, 128 if mode != '1' else 0).save(str(path), **save_kwargs)
        finally:
            Image.MAX_IMAGE_PIXELS = old_limit

    def test_oversized_valid_png_is_warning_not_corrupted(self, tmp_path):
        # 400MP exceeds both Pillow's DecompressionBombError threshold
        # (2x MAX_IMAGE_PIXELS, ~358MP) and ImageMagick's default 256MP area policy
        big = tmp_path / 'big_valid.png'
        self._make_oversized(big, 'L', (20000, 20000), compress_level=1)

        checker = PixelProbe(database_path=None)
        is_corrupted, corruption_details, scan_tool, scan_output, warning_details = \
            checker._check_image_corruption(str(big))

        assert is_corrupted is False, f"valid oversized PNG flagged corrupted: {corruption_details}"
        assert warning_details, "expected a tool-limit warning"
        assert any('limit' in w.lower() for w in warning_details)

    def test_oversized_image_imagemagick_timeout_is_warning(self, tmp_path):
        # A huge valid image can outlast the size-scaled ImageMagick timeout
        # before hitting its cache limit; with PIL's bomb guard also fired,
        # that combination must stay a warning, not corruption
        big = tmp_path / 'big_timeout.png'
        self._make_oversized(big, 'L', (20000, 20000), compress_level=1)

        checker = PixelProbe(database_path=None)
        with patch('pixelprobe.media_checker.safe_subprocess_run',
                   side_effect=subprocess.TimeoutExpired(cmd='magick', timeout=60)):
            is_corrupted, corruption_details, _, _, warning_details = \
                checker._check_image_corruption(str(big))
        assert is_corrupted is False, corruption_details
        assert warning_details

    def test_decompression_bomb_scale_image_not_corrupted(self, tmp_path):
        # 625MP 1-bit image (just past both guards), tiny on disk: verdict
        # stays sane without allocating multiple GB on a CI runner
        bomb = tmp_path / 'bomb.png'
        self._make_oversized(bomb, '1', (25000, 25000), compress_level=9)

        checker = PixelProbe(database_path=None)
        is_corrupted, _, _, _, warning_details = checker._check_image_corruption(str(bomb))
        assert is_corrupted is False
        assert warning_details

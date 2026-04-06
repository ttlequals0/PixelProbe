"""
Tests for JPEG pixel corruption detection.

Tests pass PIL Image objects directly to _check_jpeg_pixel_corruption()
since the method now accepts an already-loaded image (no file I/O).
"""

from PIL import Image

from pixelprobe.media_checker import PixelProbe


class TestJpegPixelCorruption:
    """Tests for JPEG pixel corruption detection."""

    def test_jpeg_pixel_corruption_sustained_chaos(self):
        """Detect corruption via sustained chaotic rows (rainbow garbage)."""
        checker = PixelProbe()

        img = Image.new('RGB', (200, 200), (128, 128, 128))
        pixels = img.load()
        colors = [(255, 0, 0), (0, 0, 255), (0, 255, 0), (255, 255, 0)]
        for y in range(160, 200):
            c = colors[y % len(colors)]
            for x in range(200):
                pixels[x, y] = c

        is_corrupted, details, output = checker._check_jpeg_pixel_corruption(img)
        assert is_corrupted is True
        assert any('sustained chaos' in d for d in details)

    def test_jpeg_pixel_corruption_solid_fill(self):
        """Detect corruption via solid fill streak preceded by chaos."""
        checker = PixelProbe()

        img = Image.new('RGB', (200, 200), (100, 150, 200))
        pixels = img.load()
        chaos_colors = [(255, 0, 0), (0, 0, 255), (0, 255, 0), (255, 255, 0)]
        for y in range(110, 120):
            c = chaos_colors[y % len(chaos_colors)]
            for x in range(200):
                pixels[x, y] = c
        for y in range(120, 200):
            for x in range(200):
                pixels[x, y] = (128, 128, 128)

        is_corrupted, details, output = checker._check_jpeg_pixel_corruption(img)
        assert is_corrupted is True
        assert any('solid fill' in d for d in details)

    def test_jpeg_high_contrast_no_false_positive(self):
        """YouTube-style thumbnail with sharp text boundary should not trigger."""
        checker = PixelProbe()

        img = Image.new('RGB', (200, 200))
        pixels = img.load()
        for y in range(0, 50):
            for x in range(200):
                pixels[x, y] = (10, 10, 10)
        for y in range(50, 200):
            for x in range(200):
                pixels[x, y] = ((x * 3 + y) % 256, (y * 2) % 256, (x + y * 3) % 256)

        is_corrupted, details, output = checker._check_jpeg_pixel_corruption(img)
        assert is_corrupted is False
        assert any('PASSED' in line for line in output)

    def test_jpeg_clean_no_false_positive(self):
        """Clean gradient should not trigger corruption."""
        checker = PixelProbe()

        img = Image.new('RGB', (200, 200))
        pixels = img.load()
        for y in range(200):
            for x in range(200):
                pixels[x, y] = (x % 256, y % 256, (x + y) % 256)

        is_corrupted, details, output = checker._check_jpeg_pixel_corruption(img)
        assert is_corrupted is False
        assert len(details) == 0
        assert any('PASSED' in line for line in output)

    def test_jpeg_pixel_analysis_varied_image_no_false_positive(self):
        """Varied image content should not false-positive regardless of format."""
        checker = PixelProbe()

        img = Image.new('RGB', (100, 100))
        pixels = img.load()
        for y in range(100):
            for x in range(100):
                pixels[x, y] = (x * 2 % 256, y * 2 % 256, (x + y) % 256)

        is_corrupted, details, output = checker._check_jpeg_pixel_corruption(img)
        assert is_corrupted is False

    def test_jpeg_pixel_analysis_skipped_large_dimensions(self):
        """High-resolution images should be skipped to avoid OOM."""
        checker = PixelProbe()

        class FakeImg:
            mode = 'RGB'
            size = (8000, 6000)  # 48MP -- exceeds 30MP guard
            def load(self):
                return None
            def convert(self, mode):
                return self

        is_corrupted, details, output = checker._check_jpeg_pixel_corruption(FakeImg())
        assert is_corrupted is False
        assert any('too large' in line for line in output)

    def test_jpeg_pixel_analysis_skipped_small_image(self):
        """Very small images should be skipped."""
        checker = PixelProbe()

        img = Image.new('RGB', (5, 5), (128, 128, 128))
        is_corrupted, details, output = checker._check_jpeg_pixel_corruption(img)
        assert is_corrupted is False
        assert any('too small' in line for line in output)

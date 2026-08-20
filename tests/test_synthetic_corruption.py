"""
Synthetic corruption matrix: truncations, mid-stream damage, format confusion,
and formats without committed fixtures (SVG, PSD, progressive JPEG, animated
WebP). Fixtures are generated at test time from the committed media samples,
reproducing the real failure modes this tool exists to catch (partial writes,
disk errors, incomplete transfers) rather than codec-specific re-encodes.

Verdict expectations were verified against the installed ffmpeg/ImageMagick/
Pillow. Known detection limits, asserted as such rather than papered over:
- Mid-stream damage needs to hit multiple regions; a single overwritten span
  in an MP4 can land entirely in tolerated data.
- Animated WebP content damage is undetectable (VP8 decodes garbage without
  erroring); only structural truncation is caught.
"""

import importlib.util
import os
import subprocess
from shutil import which

import pytest
from PIL import Image

from pixelprobe.media_checker import IMAGEMAGICK_BINARY, PixelProbe

SAMPLES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures', 'media_samples')

# media_samples is not a package; load the committed fixture generator so the
# test-time damage recipe cannot drift from the committed-fixture recipe
_spec = importlib.util.spec_from_file_location(
    'generate_corrupted_fixtures',
    os.path.join(SAMPLES_DIR, 'generate_corrupted_fixtures.py'))
_fixture_gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture_gen)
scattered_bytes = _fixture_gen.scattered_bytes

VALID_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
             b'<rect width="10" height="10" fill="red"/></svg>')


def _read(name):
    with open(os.path.join(SAMPLES_DIR, name), 'rb') as f:
        return f.read()


@pytest.fixture(scope='module')
def synthetic_dir(tmp_path_factory):
    """Generate every synthetic case once for the module."""
    dest = tmp_path_factory.mktemp('synthetic_media')

    def write(name, data):
        path = dest / name
        path.write_bytes(data)
        return path

    mp4 = _read('valid.mp4')
    mkv = _read('valid.mkv')
    jpg = _read('valid.jpg')
    png = _read('valid.png')

    paths = {
        'trunc_header_jpg': write('trunc_header.jpg', jpg[:64]),
        'trunc_header_png': write('trunc_header.png', png[:64]),
        'trunc_header_mp4': write('trunc_header.mp4', mp4[:64]),
        'trunc_header_mkv': write('trunc_header.mkv', mkv[:64]),
        'trunc_half_mp4': write('trunc_half.mp4', mp4[:len(mp4) // 2]),
        'trunc_tail_mkv': write('trunc_tail.mkv', mkv[:-4096]),
        'empty_mp4': write('empty.mp4', b''),
        'empty_jpg': write('empty.jpg', b''),
        'png_as_jpg': write('actually_png.jpg', png),
        'mdat_damaged_mp4': write('mdat_damaged.mp4', scattered_bytes(mp4, stride=8192, span=1024, seed=7)),
        'valid_svg': write('valid.svg', VALID_SVG),
        'corrupted_svg': write('corrupted.svg', VALID_SVG[:40]),
    }

    prog = dest / 'progressive.jpg'
    Image.new('RGB', (200, 200), (200, 50, 50)).save(str(prog), progressive=True, quality=85)
    prog_bytes = prog.read_bytes()
    paths['valid_progressive_jpg'] = prog
    paths['trunc_progressive_jpg'] = write('trunc_progressive.jpg',
                                           prog_bytes[:int(len(prog_bytes) * 0.7)])

    if which(IMAGEMAGICK_BINARY):
        psd = dest / 'valid.psd'
        subprocess.run([IMAGEMAGICK_BINARY, os.path.join(SAMPLES_DIR, 'valid.png'), str(psd)],
                       check=True, capture_output=True)
        paths['valid_psd'] = psd
        paths['corrupted_psd'] = write('corrupted.psd', psd.read_bytes()[:200])

    if which('ffmpeg'):
        anim = dest / 'valid_anim.webp'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc=duration=2:size=64x64:rate=10', '-y', str(anim)],
                       check=True, capture_output=True)
        anim_bytes = anim.read_bytes()
        paths['valid_anim_webp'] = anim
        paths['trunc_anim_webp'] = write('trunc_anim.webp',
                                         anim_bytes[:int(len(anim_bytes) * 0.6)])

    return paths


@pytest.fixture(scope='module')
def checker():
    return PixelProbe()


def _scan(checker, paths, key):
    if key not in paths:
        pytest.skip(f'{key} fixture unavailable (missing tool)')
    result = checker.scan_file(str(paths[key]))
    assert result is not None
    return result


@pytest.mark.real_media
class TestTruncation:
    @pytest.mark.timeout(120)
    @pytest.mark.parametrize('key', ['trunc_header_jpg', 'trunc_header_png',
                                     'trunc_header_mp4', 'trunc_header_mkv'])
    def test_header_truncation_detected(self, checker, synthetic_dir, key):
        result = _scan(checker, synthetic_dir, key)
        assert result['is_corrupted'], f'{key} not flagged: {result.get("corruption_details")}'

    @pytest.mark.timeout(120)
    @pytest.mark.parametrize('key', ['trunc_half_mp4', 'trunc_tail_mkv'])
    def test_body_truncation_flagged(self, checker, synthetic_dir, key):
        # Decoders conceal clean-cut truncation of these containers; the
        # frame-count-vs-metadata warning is the honest signal
        result = _scan(checker, synthetic_dir, key)
        assert result['is_corrupted'] or result.get('warning_details'), \
            f'{key} produced neither corruption nor warning'

    @pytest.mark.timeout(60)
    @pytest.mark.parametrize('key', ['empty_mp4', 'empty_jpg'])
    def test_zero_byte_files_flagged(self, checker, synthetic_dir, key):
        result = _scan(checker, synthetic_dir, key)
        assert result['is_corrupted']


@pytest.mark.real_media
class TestMidStreamDamage:
    @pytest.mark.timeout(120)
    def test_scattered_mdat_damage_detected(self, checker, synthetic_dir):
        # Validates the deep-decode path catches mid-stream damage, not just
        # header damage (header/moov region is left intact)
        result = _scan(checker, synthetic_dir, 'mdat_damaged_mp4')
        assert result['is_corrupted']


@pytest.mark.real_media
class TestFormatConfusion:
    @pytest.mark.timeout(60)
    def test_png_renamed_to_jpg_is_not_corrupted(self, checker, synthetic_dir):
        # Content is a valid PNG; a wrong extension must not produce a
        # corruption verdict or a crash
        result = _scan(checker, synthetic_dir, 'png_as_jpg')
        assert not result['is_corrupted'], result.get('corruption_details')


@pytest.mark.real_media
class TestUncoveredImageFormats:
    @pytest.mark.timeout(60)
    def test_valid_svg_clean(self, checker, synthetic_dir):
        result = _scan(checker, synthetic_dir, 'valid_svg')
        assert not result['is_corrupted'], result.get('corruption_details')

    @pytest.mark.timeout(60)
    def test_malformed_svg_detected(self, checker, synthetic_dir):
        result = _scan(checker, synthetic_dir, 'corrupted_svg')
        assert result['is_corrupted']

    @pytest.mark.timeout(60)
    def test_valid_psd_clean(self, checker, synthetic_dir):
        result = _scan(checker, synthetic_dir, 'valid_psd')
        assert not result['is_corrupted'], result.get('corruption_details')

    @pytest.mark.timeout(60)
    def test_truncated_psd_detected(self, checker, synthetic_dir):
        result = _scan(checker, synthetic_dir, 'corrupted_psd')
        assert result['is_corrupted']

    @pytest.mark.timeout(60)
    def test_valid_progressive_jpeg_clean(self, checker, synthetic_dir):
        result = _scan(checker, synthetic_dir, 'valid_progressive_jpg')
        assert not result['is_corrupted'], result.get('corruption_details')

    @pytest.mark.timeout(60)
    def test_truncated_progressive_jpeg_detected(self, checker, synthetic_dir):
        result = _scan(checker, synthetic_dir, 'trunc_progressive_jpg')
        assert result['is_corrupted']

    @pytest.mark.timeout(120)
    def test_valid_animated_webp_clean(self, checker, synthetic_dir):
        result = _scan(checker, synthetic_dir, 'valid_anim_webp')
        assert not result['is_corrupted'], result.get('corruption_details')

    @pytest.mark.timeout(120)
    def test_truncated_animated_webp_detected(self, checker, synthetic_dir):
        result = _scan(checker, synthetic_dir, 'trunc_anim_webp')
        assert result['is_corrupted']


@pytest.mark.real_media
class TestDiscovery:
    @pytest.mark.timeout(60)
    def test_symlink_loop_discovery_terminates(self, checker, tmp_path):
        loop_dir = tmp_path / 'loop' / 'a'
        loop_dir.mkdir(parents=True)
        (loop_dir / 'self').symlink_to(tmp_path / 'loop' / 'a')
        (loop_dir / 'clip.mp4').write_bytes(_read('valid.mp4'))

        files = checker.discover_media_files([str(tmp_path / 'loop')])
        assert any(f.endswith('clip.mp4') for f in files)

"""Regenerate the synthesized fixtures in this directory.

Valid fixtures for formats whose original downloads were broken (the
committed files were HTML error pages) are produced with ffmpeg/PIL; their
corrupted counterparts are derived from them with deterministic damage
recipes tuned so the scanner genuinely detects each one.

Detection expectations per format (verified against ffmpeg 6/8 and
ImageMagick 6/7):
- mkv, opus, wmv, 3gp, flv, heic, heif: corruption verdict
- mpg: warning verdict only. MPEG-1 decoders conceal even heavy scattered
  damage and exit cleanly, so the frame-count-vs-metadata warning is the
  only signal PixelProbe can produce for this format.

Run from the repository root:
    python tests/fixtures/media_samples/generate_corrupted_fixtures.py
"""

import os
import random
import subprocess

SAMPLES_DIR = os.path.dirname(os.path.abspath(__file__))


def _path(name):
    return os.path.join(SAMPLES_DIR, name)


def generate_valid_fixtures():
    """Synthesize small genuine media files for formats with broken samples."""
    ffmpeg = ['ffmpeg', '-v', 'error', '-y']
    testsrc = 'testsrc=duration=2:size=320x240:rate={rate}'
    commands = [
        ffmpeg + ['-f', 'lavfi', '-i', 'testsrc=duration=2:size=176x144:rate=15',
                  '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                  '-c:v', 'h263', '-c:a', 'aac', '-shortest', _path('valid.3gp')],
        ffmpeg + ['-f', 'lavfi', '-i', testsrc.format(rate=15),
                  '-c:v', 'flv1', '-an', _path('valid.flv')],
        ffmpeg + ['-f', 'lavfi', '-i', testsrc.format(rate=25),
                  '-c:v', 'mpeg1video', '-b:v', '800k', '-an', _path('valid.mpg')],
        ffmpeg + ['-f', 'lavfi', '-i', testsrc.format(rate=15),
                  '-c:v', 'wmv2', '-an', _path('valid.wmv')],
        ffmpeg + ['-f', 'lavfi', '-i', testsrc.format(rate=25),
                  '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                  '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                  '-shortest', _path('valid.mkv')],
    ]
    for cmd in commands:
        subprocess.run(cmd, check=True)
    from PIL import Image
    Image.new('RGB', (320, 240), (60, 120, 180)).save(_path('valid.webp'), quality=80)


def corrupt_range(src, dest, offset, span, seed=42):
    """Overwrite [offset, offset+span) with deterministic pseudo-random bytes."""
    rng = random.Random(seed)
    data = bytearray(open(src, 'rb').read())
    for i in range(offset, min(offset + span, len(data))):
        data[i] = rng.randrange(256)
    open(dest, 'wb').write(data)


def corrupt_fraction(src, dest, offset_frac, span_frac, seed=42):
    size = os.path.getsize(src)
    corrupt_range(src, dest, int(size * offset_frac), max(1, int(size * span_frac)), seed)


def truncate(src, dest, fraction):
    data = open(src, 'rb').read()
    open(dest, 'wb').write(data[:int(len(data) * fraction)])


def scattered(src, dest, stride, span, seed=42, skip_head=8192):
    """Damage `span` bytes every `stride` bytes, leaving the header intact."""
    rng = random.Random(seed)
    data = bytearray(open(src, 'rb').read())
    pos = skip_head
    while pos < len(data):
        for i in range(pos, min(pos + span, len(data))):
            data[i] = rng.randrange(256)
        pos += stride
    open(dest, 'wb').write(data)


def generate_corrupted_fixtures():
    # Recipes tuned until the scanner reports each file (see module docstring)
    corrupt_fraction(_path('valid.mkv'), _path('corrupted.mkv'), 0.05, 0.80)
    corrupt_range(_path('valid.opus'), _path('corrupted.opus'), 0, 256)
    corrupt_range(_path('valid.wmv'), _path('corrupted.wmv'), 0, 256)
    truncate(_path('valid.heic'), _path('corrupted.heic'), 0.6)
    truncate(_path('valid.heif'), _path('corrupted.heif'), 0.6)
    truncate(_path('valid.3gp'), _path('corrupted.3gp'), 0.5)
    truncate(_path('valid.flv'), _path('corrupted.flv'), 0.15)
    # MPEG-1 conceals damage; this still only yields a frame-count warning
    scattered(_path('valid.mpg'), _path('corrupted.mpg'), stride=4096, span=2048)


if __name__ == '__main__':
    generate_valid_fixtures()
    generate_corrupted_fixtures()
    print('Fixtures regenerated in', SAMPLES_DIR)

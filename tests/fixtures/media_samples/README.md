# Test Media Samples

This directory contains media files for testing PixelProbe's corruption
detection: real samples from the FFmpeg sample corpus plus locally
synthesized files (see "Synthesized fixtures" below).

## Valid Files (18 formats)
### Video
- `valid.mp4` - Apple iTunes Video (turn-on-off.mp4)
- `valid.avi` - 320x240 uncompressed AVI (dance1.avi)
- `valid.mkv` - Matroska container, H.264 + AAC (synthesized; the original sample failed to decode under ffmpeg 8)
- `valid.mov` - QuickTime movie with IMA ADPCM audio
- `valid.webm` - WebM video container
- `valid.hevc` - HEVC/H.265 video stream

### Images
- `valid.jpg` - Kodak DC210 JPEG with EXIF data
- `valid.png` - Small 14x14 PNG image
- `valid.gif` - Animated GIF (synthetic)
- `valid.bmp` - 447x335 24-bit bitmap
- `valid.tiff` - 12-bit RGB TIFF image
- `valid.webp` - WebP image (synthesized; the original sample failed PIL/ImageMagick decode)

### Audio
- `valid.flac` - 16-bit stereo FLAC (Yesterday)
- `valid.wav` - 16-bit mono PCM WAV (1kHz sine)
- `valid.mp3` - MPEG Layer 3 audio
- `valid.aac` - AAC audio stream
- `valid.m4a` - AAC in MP4 container (8 channel)
- `valid.ogg` - Ogg Vorbis audio
- `valid.wma` - Windows Media Audio
- `valid.opus` - Opus audio codec
- `valid.aiff` - 16-bit PCM AIFF (synthesized)

## Corrupted/Problematic Files (17 formats)
### Video Issues
- `corrupted.mp4` - MP4 from ticket #5522
- `corrupted.avi` - AVI with msmpeg4 bug
- `corrupted.mkv` - Scattered random damage over the synthesized valid.mkv
- `corrupted.mov` - MOV with ADPCM bug
- `corrupted.webm` - WebM from roundup issue

### Image Issues
- `corrupted.jpg` - valid.jpg truncated at 50%
- `corrupted.png` - valid.png truncated at 50%
- `corrupted.gif` - valid.gif truncated at 50% (warning-level: GIF header issues are deliberately demoted)
- `corrupted.bmp` - valid.bmp truncated at 50%
- `corrupted.tiff` - TIFF with invalid strip offset size

### Audio Issues
- `corrupted.flac` - FLAC from bug #810 (milk_30sec.flac)
- `corrupted.wav` - WAV with format 0x1501
- `corrupted.mp3` - valid.mp3 truncated at 30%
- `corrupted.aac` - AAC with decoding errors
- `corrupted.m4a` - M4A from issue #1254
- `corrupted.ogg` - Ogg Vorbis with bad loop (Lumme-Badloop)
- `corrupted.wma` - Broken WMA2 file
- `corrupted.aiff` - valid.aiff with randomized header (first 256 bytes)

## Synthesized fixtures

The originally committed `valid.3gp`, `valid.flv`, `valid.mpg`, and
`valid.wmv` were 189-byte HTML error pages from failed downloads. They and
their corrupted counterparts (plus `corrupted.mkv`, `corrupted.opus`,
`corrupted.heic`, `corrupted.heif`) are now generated locally by
`generate_corrupted_fixtures.py`. The corrupted mp3/aiff/jpg/png/gif/bmp
samples originally came from the FFmpeg bug tracker, but the bugs they
exercised were in old FFmpeg rather than in the files - modern decoders
accept all six without error - so they are also generated locally by
`generate_corrupted_fixtures.py` with deterministic damage recipes; run it
from the repository root to regenerate. `valid.3g2`, `valid.mpe`,
`valid.mpeg`, and `valid.mpv` remain symlinks to their sibling formats.

Detection expectations: every synthesized `corrupted.*` file produces a
corruption verdict except `corrupted.mpg` - MPEG-1 decoders conceal even
heavy scattered damage and exit cleanly, so PixelProbe's only signal for
that format is the frame-count-vs-metadata warning.

`valid.mov` is a sparse-video QuickTime sample: 244 real video frames over
240 seconds while container metadata declares 25fps. It is kept
deliberately as the regression case proving frame-count mismatches must
never produce a corruption verdict.

## Sources
- Valid files: https://samples.ffmpeg.org/
- Corrupted files: https://samples.ffmpeg.org/ffmpeg-bugs/
- File list: https://samples.ffmpeg.org/allsamples.txt
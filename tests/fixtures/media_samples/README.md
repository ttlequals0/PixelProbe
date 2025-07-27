# Test Media Samples

This directory contains real media files from FFmpeg samples for testing PixelProbe's corruption detection.

## Valid Files (18 formats)
### Video
- `valid.mp4` - Apple iTunes Video (turn-on-off.mp4)
- `valid.avi` - 320x240 uncompressed AVI (dance1.avi)
- `valid.mkv` - Matroska video container
- `valid.mov` - QuickTime movie with IMA ADPCM audio
- `valid.webm` - WebM video container
- `valid.hevc` - HEVC/H.265 video stream

### Images
- `valid.jpg` - Kodak DC210 JPEG with EXIF data
- `valid.png` - Small 14x14 PNG image
- `valid.gif` - Animated GIF (synthetic)
- `valid.bmp` - 447x335 24-bit bitmap
- `valid.tiff` - 12-bit RGB TIFF image
- `valid.webp` - WebP image (Big Buck Bunny title)

### Audio
- `valid.flac` - 16-bit stereo FLAC (Yesterday)
- `valid.wav` - 16-bit mono PCM WAV (1kHz sine)
- `valid.mp3` - MPEG Layer 3 audio
- `valid.aac` - AAC audio stream
- `valid.m4a` - AAC in MP4 container (8 channel)
- `valid.ogg` - Ogg Vorbis audio
- `valid.wma` - Windows Media Audio
- `valid.opus` - Opus audio codec
- `valid.aiff` - Audio Interchange File Format

## Corrupted/Problematic Files (17 formats)
### Video Issues
- `corrupted.mp4` - MP4 from ticket #5522
- `corrupted.avi` - AVI with msmpeg4 bug
- `corrupted.mkv` - Corrupted Matroska container
- `corrupted.mov` - MOV with ADPCM bug
- `corrupted.webm` - WebM from roundup issue

### Image Issues
- `corrupted.jpg` - Small/incomplete JPEG (authentica.jpg)
- `corrupted.png` - PNG with known bug (pngbug_001)
- `corrupted.gif` - Broken GIF (ban4[1].gif)
- `corrupted.bmp` - BMP from bug #874 (correct_rgb_image.bmp)
- `corrupted.tiff` - TIFF with invalid strip offset size

### Audio Issues
- `corrupted.flac` - FLAC from bug #810 (milk_30sec.flac)
- `corrupted.wav` - WAV with format 0x1501
- `corrupted.mp3` - MP3 with broken first frame
- `corrupted.aac` - AAC with decoding errors
- `corrupted.m4a` - M4A from issue #1254
- `corrupted.ogg` - Ogg Vorbis with bad loop (Lumme-Badloop)
- `corrupted.wma` - Broken WMA2 file
- `corrupted.aiff` - Invalid AIFF with no common chunk

## Sources
- Valid files: https://samples.ffmpeg.org/
- Corrupted files: https://samples.ffmpeg.org/ffmpeg-bugs/
- File list: https://samples.ffmpeg.org/allsamples.txt
#!/usr/bin/env python3
"""Download missing media samples from FFmpeg samples."""

import os
import subprocess
import urllib.request

# Priority formats to download
PRIORITY_FORMATS = {
    # Video
    'flv': 'https://samples.ffmpeg.org/FLV/flash_video_300_exemplar_1.flv',
    'wmv': 'https://samples.ffmpeg.org/WMV/wm8_2.wmv',
    '3gp': 'https://samples.ffmpeg.org/3gp/sample.3gp',
    'mpeg': 'https://samples.ffmpeg.org/MPEG2/t.mpg',
    'vob': 'https://samples.ffmpeg.org/MPEG-VOB/Cult-TrailerHD.vob',
    
    # Images
    'ico': 'https://samples.ffmpeg.org/image-samples/lena.ico',
    'svg': 'https://samples.ffmpeg.org/image-samples/free_splash.svg',
    'psd': 'https://samples.ffmpeg.org/image-samples/layers.psd',
    'jpeg': 'valid.jpg',  # Symlink to existing jpg
    'tif': 'valid.tiff',  # Symlink to existing tiff
    
    # Audio
    'wave': 'valid.wav',  # Symlink to existing wav
    'ogg': 'https://samples.ffmpeg.org/ogg/Welcome.ogg',
    'ac3': 'https://samples.ffmpeg.org/A-codecs/AC3/devils_canyon_lossless-trunc.ac3',
    'dts': 'https://samples.ffmpeg.org/DTS/dts.ts',
    'ra': 'https://samples.ffmpeg.org/real/RA_14400_test.ra',
    'amr': 'https://samples.ffmpeg.org/AMR/sample.amr',
    'gsm': 'https://samples.ffmpeg.org/A-codecs/GSM/sample-gsm-8000.mov',
    
    # Lossless/Special
    'ape': 'https://samples.ffmpeg.org/A-codecs/lossless/luckynight-partial.ape',
    'tta': 'https://samples.ffmpeg.org/tta/sample.tta',
}

# Corrupted samples
CORRUPTED_FORMATS = {
    'flv': 'https://samples.ffmpeg.org/ffmpeg-bugs/roundup/issue1690/yt.flv',
    'wmv': 'https://samples.ffmpeg.org/A-codecs/WMA/broken-new/tc11_01.wmv',
    '3gp': 'https://samples.ffmpeg.org/ffmpeg-bugs/trac/ticket3514/untitled.3gp',
}

def download_file(url, filename):
    """Download a file from URL."""
    if os.path.exists(filename):
        print(f"✓ {filename} already exists")
        return True
    
    print(f"Downloading {filename} from {url}")
    try:
        urllib.request.urlretrieve(url, filename)
        print(f"✓ Downloaded {filename}")
        return True
    except Exception as e:
        print(f"✗ Failed to download {filename}: {e}")
        return False

def create_symlink(source, dest):
    """Create a symlink."""
    if os.path.exists(dest):
        print(f"✓ {dest} already exists")
        return
    
    if os.path.exists(source):
        os.symlink(os.path.basename(source), dest)
        print(f"✓ Created symlink {dest} -> {source}")
    else:
        print(f"✗ Cannot create symlink {dest}: source {source} doesn't exist")

def main():
    """Download missing samples."""
    os.chdir(os.path.dirname(__file__))
    
    print("=== Downloading Valid Samples ===")
    for fmt, source in PRIORITY_FORMATS.items():
        filename = f"valid.{fmt}"
        
        if source.startswith('valid.'):
            # Create symlink
            create_symlink(source, filename)
        else:
            # Download file
            download_file(source, filename)
    
    print("\n=== Downloading Corrupted Samples ===")
    for fmt, url in CORRUPTED_FORMATS.items():
        filename = f"corrupted.{fmt}"
        download_file(url, filename)
    
    print("\n=== Creating Additional Formats ===")
    
    # Create HEIC/HEIF from JPEG using ImageMagick (if available)
    if os.path.exists('valid.jpg'):
        for ext in ['heic', 'heif']:
            if not os.path.exists(f'valid.{ext}'):
                try:
                    subprocess.run(['convert', 'valid.jpg', f'valid.{ext}'], check=True)
                    print(f"✓ Created valid.{ext}")
                except Exception as e:
                    print(f"✗ Failed to create valid.{ext}: {e}")
    
    # Create m4v from mp4
    if not os.path.exists('valid.m4v') and os.path.exists('valid.mp4'):
        os.symlink('valid.mp4', 'valid.m4v')
        print("✓ Created symlink valid.m4v -> valid.mp4")

if __name__ == "__main__":
    main()
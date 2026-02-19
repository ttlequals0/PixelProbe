"""
Helper utilities for PixelProbe
"""

import os
import logging
from pixelprobe.constants import VIDEO_EXTENSIONS, IMAGE_EXTENSIONS, AUDIO_EXTENSIONS

logger = logging.getLogger(__name__)

def format_file_size(size_bytes):
    """Format file size in human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

def is_media_file(file_path):
    """Check if file is a supported media file"""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in VIDEO_EXTENSIONS or ext in IMAGE_EXTENSIONS or ext in AUDIO_EXTENSIONS


def get_configured_scan_paths():
    """Read scan paths from DB (ScanConfiguration), falling back to SCAN_PATHS env var.

    Returns a list of path strings. May be empty if nothing is configured.
    """
    try:
        from models import ScanConfiguration
        configs = ScanConfiguration.query.filter_by(is_active=True).all()
        paths = [config.path for config in configs if config.path]
        if paths:
            return paths
    except Exception:
        pass

    scan_paths_env = os.environ.get('SCAN_PATHS', '')
    return [p.strip() for p in scan_paths_env.split(',') if p.strip()]

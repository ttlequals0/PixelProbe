"""
Helper utilities for PixelProbe
"""

import os
import pytz
import logging

logger = logging.getLogger(__name__)

def get_timezone():
    """Get configured timezone, default to UTC"""
    APP_TIMEZONE = os.environ.get('TZ', 'UTC')
    try:
        tz = pytz.timezone(APP_TIMEZONE)
        return tz
    except pytz.exceptions.UnknownTimeZoneError:
        logger.warning(f"Unknown timezone '{APP_TIMEZONE}', falling back to UTC")
        return pytz.UTC

def format_file_size(size_bytes):
    """Format file size in human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

def is_media_file(file_path):
    """Check if file is a supported media file"""
    # Video extensions
    video_extensions = {
        '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', 
        '.mpeg', '.3gp', '.3g2', '.mxf', '.roq', '.nsv', '.f4v', '.f4p', '.f4a', 
        '.f4b', '.mts', '.m2ts', '.ts', '.vob', '.ogv', '.drc', '.gif', '.gifv', 
        '.mng', '.qt', '.yuv', '.rm', '.rmvb', '.asf', '.amv', '.m2v', '.svi', 
        '.mpc', '.mpv', '.mpe', '.m1v', '.m2p', '.m2t', '.m2ts', '.mts', '.mt2s',
        '.rec', '.divx', '.xvid', '.evo', '.fli', '.flc', '.tod', '.avchd'
    }
    
    # Image extensions  
    image_extensions = {
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp', '.ico',
        '.heic', '.heif', '.raw', '.arw', '.cr2', '.cr3', '.nef', '.nrw', '.orf',
        '.rw2', '.pef', '.srw', '.x3f', '.erf', '.kdc', '.dcs', '.rw1', '.raw',
        '.dng', '.raf', '.dcr', '.ptx', '.pxn', '.bay', '.crw', '.3fr', '.sr2',
        '.srf', '.mef', '.mos', '.gpr', '.mrw', '.mdc', '.rwl', '.iiq', '.cap'
    }
    
    # Audio extensions
    audio_extensions = {
        '.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a', '.opus', '.ape',
        '.ac3', '.dts', '.alac', '.aiff', '.au', '.m4b', '.m4p', '.m4r', '.mka',
        '.mpa', '.mpc', '.mp2', '.ra', '.tta', '.voc', '.vox', '.wv', '.8svx'
    }
    
    ext = os.path.splitext(file_path)[1].lower()
    return ext in video_extensions or ext in image_extensions or ext in audio_extensions
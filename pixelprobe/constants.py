"""
PixelProbe Constants

Central location for all application constants including supported file formats.
This eliminates duplication across the codebase and ensures consistency.
"""

# Video formats - including HEVC/H.265 and professional formats
VIDEO_EXTENSIONS = [
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v',
    '.hevc', '.h265',  # HEVC/H.265 formats
    '.mxf', '.prores',  # ProRes format
    '.dnxhd', '.dnxhr',  # DNxHD/DNxHR formats
    '.mts', '.m2ts', '.avchd',  # AVCHD formats
    '.mpg', '.mpeg', '.vob',  # MPEG formats
    '.3gp', '.3g2',  # Mobile formats
    '.f4v', '.f4p',  # Flash formats
    '.ogv', '.ogg',  # Ogg video
    '.rm', '.rmvb',  # RealMedia
    '.asf', '.amv',  # Other formats
    '.m2v', '.svi', '.mpe', '.mpv', '.m4p'
]

# Image formats - including HEIC/HEIF and RAW formats
IMAGE_EXTENSIONS = [
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp',
    '.heic', '.heif',  # Apple HEIC/HEIF formats
    '.cr2', '.cr3',  # Canon RAW
    '.nef', '.nrw',  # Nikon RAW
    '.arw', '.srf', '.sr2',  # Sony RAW
    '.dng',  # Adobe Digital Negative
    '.orf',  # Olympus RAW
    '.rw2',  # Panasonic RAW
    '.pef', '.ptx',  # Pentax RAW
    '.raf',  # Fujifilm RAW
    '.raw',  # Generic RAW
    '.x3f',  # Sigma RAW
    '.dcr', '.kdc',  # Kodak RAW
    '.mos',  # Leaf RAW
    '.psd',  # Photoshop
    '.ico',  # Icon files
    '.svg',  # Scalable Vector Graphics
    '.exr',  # OpenEXR
    '.pbm', '.pgm', '.ppm', '.pnm',  # Netpbm formats
    '.hdr', '.pic',  # Radiance HDR
    '.fts', '.fits',  # FITS (astronomy)
]

# Audio formats - Complete audio support
AUDIO_EXTENSIONS = [
    '.mp3',  # MPEG Audio Layer 3
    '.flac',  # Free Lossless Audio Codec
    '.wav', '.wave',  # Waveform Audio
    '.aac', '.m4a',  # Advanced Audio Coding
    '.ogg', '.oga', '.opus',  # Ogg Vorbis/Opus
    '.wma',  # Windows Media Audio
    '.aiff', '.aif', '.aifc',  # Audio Interchange File Format
    '.ape',  # Monkey's Audio
    '.wv',  # WavPack
    '.tta',  # True Audio
    '.m4b',  # Audiobook format
    '.mka',  # Matroska Audio
    '.dsf', '.dff',  # DSD formats
    '.au', '.snd',  # Sun/NeXT audio
    '.voc',  # Creative Voice
    '.amr',  # Adaptive Multi-Rate
    '.ac3',  # Dolby Digital
    '.dts',  # DTS audio
    '.ra', '.ram',  # RealAudio
    '.mid', '.midi',  # MIDI (if needed)
    '.caf',  # Core Audio Format
    '.gsm',  # GSM audio
]

# All supported formats combined
SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS + IMAGE_EXTENSIONS + AUDIO_EXTENSIONS

# Scan phases - standardized phase names
SCAN_PHASES = {
    'IDLE': 'idle',
    'INITIALIZING': 'initializing',
    'DISCOVERING': 'discovering',
    'ADDING': 'adding',
    'SCANNING': 'scanning',
    'COMPLETED': 'completed',
    'ERROR': 'error',
    'CRASHED': 'crashed',
    'CANCELLED': 'cancelled'
}

# Active scan phases (phases where scan is considered running)
ACTIVE_SCAN_PHASES = ['initializing', 'discovering', 'adding', 'scanning']

# Terminal scan phases (phases where scan has ended)
TERMINAL_SCAN_PHASES = ['idle', 'completed', 'error', 'crashed', 'cancelled']

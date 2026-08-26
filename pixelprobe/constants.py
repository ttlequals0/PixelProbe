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

# AppConfig keys
CONFIG_LOG_RETENTION_DAYS = 'log_retention_days'
CONFIG_LOG_EXCLUDE_LOGGERS = 'log_exclude_loggers'

# Default excluded loggers for database log storage.
# celery.app.trace emits one INFO row per task ("succeeded in Xs" with the
# full result): at ~850 tasks/s during maintenance runs that is ~150MB of WAL
# per 5 minutes, driving checkpoint IO storms that stall the producer loops
DEFAULT_LOG_EXCLUDE_LOGGERS = 'urllib3,werkzeug,celery.worker.strategy,celery.app.trace,celery.bootsteps,kombu,amqp'

# Sentinel value for system (non-scan) logs
SYSTEM_LOG_ID = 'system'


# --------------------------------------------------------------------------
# Scanner settings registry
#
# One definition per setting, and everything derives from it: the API's
# validation, the settings UI, the seeding migration, and the docs table. A
# setting added here needs no change anywhere else to become editable.
#
# `legacy_env` names the environment variable the setting used to read. It is
# consulted once, by the seeding migration, so an existing deployment keeps its
# tuning; after that the stored value is authoritative and the variable is
# ignored.
# --------------------------------------------------------------------------

SETTING_GROUP_DETECTION = 'detection'
SETTING_GROUP_PERFORMANCE = 'performance'
SETTING_GROUP_TIMEOUTS = 'timeouts'

SETTING_GROUPS = [
    {
        'key': SETTING_GROUP_DETECTION,
        'label': 'Detection',
        'help': 'What counts as a finding. These decide which files get flagged.',
    },
    {
        'key': SETTING_GROUP_PERFORMANCE,
        'label': 'Performance',
        'help': 'Limits on how much work one file may cost. These bound scan time, not accuracy.',
    },
    {
        'key': SETTING_GROUP_TIMEOUTS,
        'label': 'Timeouts',
        'help': 'How long to wait on storage and tools before giving up on a file.',
    },
]

SCANNER_SETTINGS = [
    {
        'key': 'detection.freeze_detection_enabled',
        'group': SETTING_GROUP_DETECTION,
        'label': 'Detect frozen video',
        'help': 'Look for stretches where the picture stops changing. This is a full decode of '
                'every video and is the slowest part of a scan.',
        'type': 'bool',
        'default': True,
        'legacy_env': 'FREEZE_DETECTION_ENABLED',
    },
    {
        'key': 'detection.freeze_min_duration_secs',
        'group': SETTING_GROUP_DETECTION,
        'label': 'Shortest freeze to report',
        'help': 'Seconds. Animation holds a drawing still for several seconds at a time, so a low '
                'value reports ordinary cartoons. Raise it to report only longer freezes.',
        'type': 'float',
        'default': 7.0,
        'min': 1.0,
        'max': 600.0,
        'unit': 'seconds',
    },
    {
        'key': 'detection.freeze_uncorroborated_min_secs',
        'group': SETTING_GROUP_DETECTION,
        'label': 'Longest freeze to excuse',
        'help': 'Seconds. A frozen stretch with its packets present and its frames decodable '
                'stopped on purpose, so it is not reported. Past this length it is reported '
                'anyway, in case a source recorded a genuinely stuck picture.',
        'type': 'float',
        'default': 60.0,
        'min': 10.0,
        'max': 3600.0,
        'unit': 'seconds',
    },
    {
        'key': 'detection.data_hole_alloc_ratio',
        'group': SETTING_GROUP_DETECTION,
        'label': 'Incomplete file check threshold',
        'help': 'A file storing less data than its length claims is opened and checked for '
                'unwritten regions. Compressing filesystems store healthy files in less space, so '
                'this only decides which files are worth checking.',
        'type': 'float',
        'default': 0.90,
        'min': 0.0,
        'max': 1.0,
        'legacy_env': 'DATA_HOLE_ALLOC_RATIO',
    },
    {
        'key': 'detection.data_hole_min_pct',
        'group': SETTING_GROUP_DETECTION,
        'label': 'Unwritten share that means damage',
        'help': 'Percent of a file that must be unwritten before it is called incomplete.',
        'type': 'float',
        'default': 1.0,
        'min': 0.0,
        'max': 100.0,
        'unit': 'percent',
        'legacy_env': 'DATA_HOLE_MIN_PCT',
    },
    {
        'key': 'performance.temporal_sample_secs',
        'group': SETTING_GROUP_PERFORMANCE,
        'label': 'Length of each sampled window',
        'help': 'Seconds decoded at each of three points in a large file when looking for timing '
                'anomalies.',
        'type': 'int',
        'default': 10,
        'min': 5,
        'max': 120,
        'unit': 'seconds',
        'legacy_env': 'TEMPORAL_SAMPLE_SECS',
    },
    {
        'key': 'performance.temporal_min_frames',
        'group': SETTING_GROUP_PERFORMANCE,
        'label': 'Frames needed to judge a window',
        'help': 'Below this many decoded frames the measurements are noise and no verdict is '
                'recorded.',
        'type': 'int',
        'default': 100,
        'min': 1,
        'max': 10000,
        'legacy_env': 'TEMPORAL_MIN_FRAMES',
    },
    {
        'key': 'timeouts.temporal_sample_timeout_secs',
        'group': SETTING_GROUP_TIMEOUTS,
        'label': 'Sampled window timeout',
        'help': 'Seconds to wait on one sampled window. Raise this on a busy host, where a timeout '
                'means contention rather than a bad file.',
        'type': 'int',
        'default': 30,
        'min': 10,
        'max': 3600,
        'unit': 'seconds',
        'legacy_env': 'TEMPORAL_SAMPLE_TIMEOUT_SECS',
    },
    {
        'key': 'timeouts.ffprobe_timeout_secs',
        'group': SETTING_GROUP_TIMEOUTS,
        'label': 'Metadata read timeout',
        'help': 'Seconds to wait when reading a file\'s metadata. Raise it on slow storage.',
        'type': 'int',
        'default': 120,
        'min': 10,
        'max': 3600,
        'unit': 'seconds',
        'legacy_env': 'FFPROBE_TIMEOUT_SECS',
    },
    {
        'key': 'timeouts.file_read_timeout_secs',
        'group': SETTING_GROUP_TIMEOUTS,
        'label': 'File read timeout',
        'help': 'Seconds to wait on a raw read before treating the file as unreadable and moving '
                'on. The hashing deadline scales up with file size on top of this.',
        'type': 'int',
        'default': 60,
        'min': 10,
        'max': 3600,
        'unit': 'seconds',
        'legacy_env': 'FILE_READ_TIMEOUT_SECS',
    },
]

SCANNER_SETTINGS_BY_KEY = {s['key']: s for s in SCANNER_SETTINGS}

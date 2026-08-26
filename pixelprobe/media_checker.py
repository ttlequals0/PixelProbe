import errno
import os
import re
import subprocess
import magic
import logging
import hashlib
import json
import mmap
import time
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image

# Try to import pillow-heif for better HEIC support
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False
import ffmpeg
import tempfile
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from pixelprobe.utils.security import safe_subprocess_run, validate_file_path, ensure_cli_safe_path
from pixelprobe.utils.helpers import env_int, env_float
from pixelprobe.utils.integrity import apply_scan_baseline
from pixelprobe.utils.paths import is_path_under
from pixelprobe.services.settings_service import resolve_settings
from pixelprobe.utils.overrides import retire_stale_override

logger = logging.getLogger(__name__)


def _setting(key):
    """Current value of a scanner setting.

    These were module constants read from the environment at import time, so a
    change meant a restart. They are stored rows now; resolve_settings() caches
    them briefly, so reading one per file costs a dict lookup rather than a
    query, and an edit reaches a running worker without a restart.
    """
    return resolve_settings()[key]


# Hard ceiling for ffprobe metadata reads. ffmpeg-python's probe() calls
# Popen.communicate() with no timeout, so a stalled mount or crafted container
# could hang a scan worker forever; this bounds it. Env-overridable.



def _ffprobe_with_timeout(file_path, timeout=None):
    """Drop-in replacement for ffmpeg.probe() with a hard timeout.

    Runs the same ffprobe invocation ffmpeg-python uses, but through
    safe_subprocess_run so it cannot hang indefinitely. Raises ffmpeg.Error on a
    non-zero exit (matching ffmpeg.probe) and subprocess.TimeoutExpired if
    ffprobe exceeds the timeout.
    """
    if timeout is None:
        timeout = _setting('timeouts.ffprobe_timeout_secs')
    result = safe_subprocess_run(
        ['ffprobe', '-show_format', '-show_streams', '-of', 'json', ensure_cli_safe_path(file_path)],
        capture_output=True, timeout=timeout
    )
    if result.returncode != 0:
        raise ffmpeg.Error('ffprobe', result.stdout, result.stderr)
    return json.loads(result.stdout.decode('utf-8'))

def _probe_video_duration(file_path):
    """Video duration in seconds, or None if the container does not report one.

    Prefers the video stream's own duration and falls back to the container's;
    Matroska commonly omits the stream-level value. Propagates ffprobe failures
    so callers can surface an incomplete check rather than assume a clean file.
    """
    probe = _ffprobe_with_timeout(file_path)
    video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)

    for source in (video_stream, probe.get('format')):
        if source and 'duration' in source:
            try:
                duration = float(source['duration'])
            except (ValueError, TypeError):
                continue
            if duration > 0:
                return duration
    return None

# Deadline for pure-Python file reads (stat, magic, hash). Unlike the external
# tools these have no subprocess timeout, so a file on stalled storage (dead
# NFS/SMB mount, failing sector) blocks the scan worker in the kernel forever
# with no ffmpeg/ImageMagick process visible (issue #70). Env-overridable.

# Each timed-out read abandons one stuck thread (and its fd) until the kernel
# read returns. Cap how many may be live at once so a fully dead mount fails
# fast instead of exhausting the fd table one file at a time.
_MAX_ABANDONED_READ_THREADS = 32
_abandoned_read_threads = []
_abandoned_read_threads_lock = threading.Lock()


class FileReadTimeoutError(Exception):
    """A raw file read stalled past its deadline (dead mount / bad sector)."""


def _read_with_timeout(func, timeout, file_path, operation):
    """Run a blocking read in a watchdog thread with a hard deadline.

    A read stuck in uninterruptible kernel sleep cannot be interrupted or
    killed; on timeout the daemon thread is abandoned (it holds one fd until
    the read returns or the process exits) and FileReadTimeoutError is raised
    so the scan marks the file unreadable and moves on instead of hanging the
    whole chunk. Once _MAX_ABANDONED_READ_THREADS reads are stuck, new reads
    fail immediately without spawning more threads.
    """
    with _abandoned_read_threads_lock:
        _abandoned_read_threads[:] = [t for t in _abandoned_read_threads if t.is_alive()]
        if len(_abandoned_read_threads) >= _MAX_ABANDONED_READ_THREADS:
            raise FileReadTimeoutError(
                f'{operation} of {file_path} skipped - '
                f'{len(_abandoned_read_threads)} reads already stalled, '
                f'storage appears unreachable')

    result = {}

    def target():
        try:
            result['value'] = func()
        except Exception as e:
            result['error'] = e

    thread = threading.Thread(target=target, daemon=True,
                              name=f'read-watchdog:{operation}')
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        with _abandoned_read_threads_lock:
            _abandoned_read_threads.append(thread)
        raise FileReadTimeoutError(
            f'{operation} of {file_path} stalled for {timeout}s - '
            f'skipping unreadable file')
    if 'error' in result:
        raise result['error']
    return result.get('value')

# Pre-compiled patterns for parsing FFmpeg freezedetect filter output
_RE_FREEZE_START = re.compile(r'freeze_start:\s*([\d.]+)')
_RE_FREEZE_END = re.compile(r'freeze_end:\s*([\d.]+)')
_RE_FREEZE_DURATION = re.compile(r'freeze_duration:\s*([\d.]+)')

def _steady_cadence(pts, spread=2.0, minimum=8):
    """Whether timestamps tick at a steady rhythm.

    True when the large gaps between consecutive packets stay within `spread`
    times the median gap. Constant-frame-rate video is steady; variable-rate
    sources (screen recordings, slideshows) are not, and an absent stretch in
    an unsteady stream proves nothing.
    """
    if len(pts) < minimum:
        return False
    gaps = sorted(b - a for a, b in zip(pts, pts[1:]) if b > a)
    if not gaps:
        return False
    median = gaps[len(gaps) // 2]
    high = gaps[int(len(gaps) * 0.95)] if len(gaps) > 1 else median
    return median > 0 and high <= median * spread


def _parse_rate(text):
    """Parse an ffprobe rate fraction like '24000/1001'. None when unusable
    (missing, zero, or the '0/0' ffprobe emits for unknown rates)."""
    if not text:
        return None
    try:
        if '/' in str(text):
            num, den = str(text).split('/', 1)
            value = float(num) / float(den)
        else:
            value = float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def _parse_freeze_events(lines):
    """Parse freezedetect events out of pre-split FFmpeg stderr lines.

    FFmpeg emits freeze_start, then freeze_duration, then freeze_end. An event
    closes only once all three fields are in, so an end line cannot leak into
    the next event's dict, and a new start over a partial dict resyncs.

    Returns a list of {start, duration, end} dicts.
    """
    events = []
    current = {}
    for line in lines:
        if 'freezedetect' not in line.lower():
            continue

        start_match = _RE_FREEZE_START.search(line)
        if start_match:
            current = {'start': float(start_match.group(1))}

        end_match = _RE_FREEZE_END.search(line)
        if end_match:
            current['end'] = float(end_match.group(1))

        dur_match = _RE_FREEZE_DURATION.search(line)
        if dur_match:
            current['duration'] = float(dur_match.group(1))

        if len(current) == 3:
            events.append(current)
            current = {}
    return events


def _merged_frozen_seconds(events):
    """Total seconds covered by freeze events, counting overlapping spans once.

    A naive sum double-counts events that span the same stretch and can run
    past the file's own runtime.
    """
    total = 0.0
    reach = float('-inf')
    for start, end in sorted(
            (e.get('start', 0), e.get('start', 0) + e.get('duration', 0)) for e in events):
        if end > reach:
            total += end - max(start, reach)
            reach = end
    return total


# Corroboration asks what else is true where the picture stopped: are the
# packets even there, and can the decoder produce pictures? A freeze with
# neither signal is a held animation cel or a static title/end card, not
# damage. Thresholds are generous because observed real cases are extreme
# (87-100% of packets absent; thousands of decode errors in one window).
FREEZE_PACKET_ABSENT_RATIO = 0.2  # under this fraction of expected packets = missing content
FREEZE_DECODE_ERROR_MIN = 20      # more error lines than this in one window = decode failure
FREEZE_CORROBORATE_MAX_EVENTS = 12
FREEZE_PROBE_TIMEOUT_SECS = 120

_RE_DECODE_ERROR = re.compile(
    r'Invalid NAL unit size|Error splitting the input|missing picture'
    r'|Error submitting packet to decoder|error while decoding MB', re.IGNORECASE)

# A file whose allocated blocks fall short of its nominal size may have holes
# in it. Compression and dedup under-allocate healthy files too, so the ratio
# is only a cheap gate deciding which files are worth opening; the verdict
# comes from SEEK_HOLE, which reports regions that were never written rather
# than regions that happen to contain zeroes.
DATA_HOLE_MIN_SIZE = 1024 * 1024

# Pre-compiled patterns for parsing FFmpeg blackdetect filter output
_RE_BLACK_START = re.compile(r'black_start:\s*([\d.]+)')
_RE_BLACK_END = re.compile(r'black_end:\s*([\d.]+)')
_RE_BLACK_DURATION = re.compile(r'black_duration:\s*([\d.]+)')

# Pre-compiled patterns for parsing FFmpeg signalstats metadata output
_RE_SIGNALSTATS_TOUT = re.compile(r'lavfi\.signalstats\.TOUT=([\d.eE+-]+)')
_RE_SIGNALSTATS_VREP = re.compile(r'lavfi\.signalstats\.VREP=([\d.eE+-]+)')

# Stage 2 samples windows from the middle of the file instead of reading from the
# start. Intros, fades and title cards are near-static, so vertical-line
# repetition there legitimately approaches 1.0 and describes the titles rather
# than the video; credits at the tail have the same problem. Env-overridable.
TEMPORAL_SAMPLE_POSITIONS = (0.25, 0.50, 0.75)


# Below this many sampled frames the percentages are noise, so no verdict is
# issued. Judging a corruption verdict on a handful of opening frames is exactly
# the defect this stage had (issue: ffmpeg 8 false positives).

# Max "Error parsing Opus packet header" lines still treated as the benign
# ffmpeg 8 EOF artifact (one per Opus stream; verified-clean files show 1-3).
# More means mid-stream damage that a -c copy validation cannot surface
# through the exit code because audio is never decoded.
OPUS_EOF_NOTICE_MAX = env_int('OPUS_EOF_NOTICE_MAX', 4, floor=1)

# Verdict thresholds for signalstats. Per-frame values above TOUT/VREP_FRAME
# count toward the corresponding percentage, and the file is judged once that
# percentage of sampled frames is exceeded.
#
# Measured on real media: body frames of healthy files score 0.0-0.2% over
# VREP_FRAME with a per-frame max of 0.60, while a file with genuine byte
# corruption and decoder concealment scored 32.9% and clean synthetic flat
# content scored 45.5%. VREP therefore cannot separate damage from flat or
# graphic content at any per-frame threshold (extreme 10x row duplication peaks
# at 0.833), which is why exceeding VREP_PERCENT only warns while TOUT marks
# corruption.
TEMPORAL_TOUT_FRAME = 0.1
TEMPORAL_TOUT_PERCENT = 5.0
TEMPORAL_VREP_FRAME = 0.5
TEMPORAL_VREP_PERCENT = 10.0

# ImageMagick 7 renamed the CLI to 'magick' ('convert' survives only as a
# compatibility alias); prefer it, fall back for ImageMagick 6 systems.
IMAGEMAGICK_BINARY = 'magick' if shutil.which('magick') else 'convert'

def get_default_filename_patterns():
    """Get default filename patterns to exclude"""
    return [
        '._*',  # macOS resource fork files (AppleDouble format)
        '.DS_Store',  # macOS folder metadata
        'Thumbs.db',  # Windows thumbnail cache
        '.gitkeep',  # Git placeholder files
        '.placeholder'  # Common placeholder files
    ]

def load_exclusions():
    """Load exclusion patterns from exclusions.json file with default exclusions
    
    Returns:
        tuple: (excluded_paths, excluded_extensions) for backward compatibility
    """
    # Default exclusions that are always applied
    default_excluded_paths = []
    default_excluded_extensions = []
    
    try:
        # Load user-defined exclusions from file
        exclusions_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'exclusions.json')
        user_paths = []
        user_extensions = []
        
        if os.path.exists(exclusions_file):
            with open(exclusions_file, 'r') as f:
                data = json.load(f)
                user_paths = data.get('paths', [])
                user_extensions = data.get('extensions', [])
        
        # Combine default and user exclusions
        excluded_paths = list(set(default_excluded_paths + user_paths))
        excluded_extensions = list(set(default_excluded_extensions + user_extensions))
        
        return excluded_paths, excluded_extensions
    except Exception as e:
        logger.error(f"Error loading exclusions.json: {e}")
        # Return defaults on error
        return default_excluded_paths, default_excluded_extensions

def load_exclusions_with_patterns():
    """Load exclusion patterns including filename patterns.

    Reads exclusions.json once and returns all exclusion data.

    Returns:
        tuple: (excluded_paths, excluded_extensions, excluded_patterns)
    """
    default_patterns = get_default_filename_patterns()

    try:
        exclusions_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'exclusions.json')
        if os.path.exists(exclusions_file):
            with open(exclusions_file, 'r') as f:
                data = json.load(f)
                paths = list(set(data.get('paths', [])))
                extensions = list(set(data.get('extensions', [])))
                user_patterns = data.get('filename_patterns', [])
                excluded_patterns = list(set(default_patterns + user_patterns))
                return paths, extensions, excluded_patterns
    except Exception as e:
        logger.error(f"Error loading exclusions.json: {e}")

    return [], [], default_patterns


class PixelProbe:
    def __init__(self, max_workers=None, excluded_paths=None, excluded_extensions=None, database_path=None, excluded_patterns=None):
        # Lazy import to avoid circular dependency (media_checker <- pixelprobe <- media_checker)
        from pixelprobe.constants import VIDEO_EXTENSIONS, IMAGE_EXTENSIONS, AUDIO_EXTENSIONS, SUPPORTED_EXTENSIONS

        # Use centralized file format constants
        self.supported_video_formats = VIDEO_EXTENSIONS
        self.supported_image_formats = IMAGE_EXTENSIONS
        self.supported_audio_formats = AUDIO_EXTENSIONS
        self.supported_formats = SUPPORTED_EXTENSIONS
        self.max_workers = max_workers or min(4, os.cpu_count() or 1)
        self.scan_lock = threading.Lock()
        self.current_scan_file = None
        self.scan_start_time = None
        self.excluded_paths = excluded_paths or []
        self.excluded_extensions = excluded_extensions or []
        self.excluded_patterns = excluded_patterns or get_default_filename_patterns()
        self.database_path = database_path
        # Database session management - reuse connections
        self._db_engine = None
        self._db_session_factory = None
        self._init_database_connection()
        # Track failed save operations for debugging
        self.failed_saves = 0
        self.successful_saves = 0
        
    def _init_database_connection(self):
        """Initialize database connection for thread-local instances"""
        if self.database_path:
            try:
                from sqlalchemy import create_engine
                from sqlalchemy.orm import sessionmaker
                from sqlalchemy.pool import QueuePool, StaticPool
                from pixelprobe.config import PG_SESSION_TZ_UTC
                # Match driver-qualified URLs too (postgresql+psycopg2://...)
                if self.database_path.startswith(('postgresql://', 'postgresql+')):
                    # QueuePool sized to the worker count: each scan thread checks out
                    # its own connection. StaticPool shared one raw psycopg2 connection
                    # across ThreadPoolExecutor workers, which psycopg2 forbids using
                    # concurrently (sporadic save/cache failures under num_workers > 1).
                    # NullPool caused "concurrent operations not permitted" errors during
                    # progress updates. Budget: main app pool 20+40=60, workers
                    # max_workers+10 overflow ceiling, still under PostgreSQL's
                    # default 100 for any MAX_WORKERS the config allows.
                    self._db_engine = create_engine(
                        self.database_path,
                        poolclass=QueuePool,
                        pool_size=self.max_workers,
                        # Overflow headroom so a caller that forgets to size
                        # max_workers to its thread count degrades to on-demand
                        # connections instead of a 30s checkout timeout
                        max_overflow=10,
                        # No pre-ping: the scan hot path checks out a session
                        # per cache read and per save, and a per-checkout
                        # SELECT 1 across a million-file scan is real traffic;
                        # recycle covers long-lived staleness instead
                        pool_recycle=3600,
                        connect_args={
                            'connect_timeout': 10,
                            'application_name': 'pixelprobe_worker',
                            # Same UTC session pin as the Flask engine (issue #65):
                            # this engine writes scan_date/creation_date/last_modified
                            'options': PG_SESSION_TZ_UTC
                        }
                    )
                else:
                    # SQLite (tests only; production is PostgreSQL-only since v2.2.0).
                    # StaticPool is required for :memory: databases to share one
                    # connection; check_same_thread=False permits cross-thread use
                    # (and is an invalid connect arg for any other driver).
                    connect_args = {'check_same_thread': False} \
                        if self.database_path.startswith('sqlite') else {}
                    self._db_engine = create_engine(
                        self.database_path,
                        poolclass=StaticPool,
                        connect_args=connect_args
                    )
                self._db_session_factory = sessionmaker(bind=self._db_engine)
                logger.info(f"Worker database engine initialized (pool sized for {self.max_workers} workers)")
            except Exception as e:
                logger.error(f"Failed to initialize database connection: {e}")
                self._db_engine = None
                self._db_session_factory = None
    
    def _get_db_session(self):
        """Get a database session from the pool"""
        if self._db_session_factory:
            return self._db_session_factory()
        return None

    def dispose_database_connection(self):
        """Release pooled connections deterministically when a scan finishes"""
        if self._db_engine:
            self._db_engine.dispose()
    
    def discover_media_files(self, directories, max_files=None, existing_files=None, batch_check_callback=None, progress_callback=None):
        """Phase 1: Discover all supported files and return their paths (parallel version)
        
        Args:
            directories: List of directories to scan
            max_files: Maximum number of files to discover
            existing_files: (deprecated) Set of existing file paths to skip
            batch_check_callback: Function that takes a list of paths and returns set of existing ones
            progress_callback: Function to report progress
        """
        # Support both old (existing_files) and new (batch_check_callback) methods
        if existing_files is not None:
            logger.info(f"Using legacy in-memory existing_files set with {len(existing_files)} entries")
        elif batch_check_callback:
            logger.info(f"Using efficient batch database checking for duplicate detection")
        
        logger.info(f"Starting parallel file discovery in {len(directories)} directories")
        
        # Use parallel discovery for multiple paths
        if len(directories) > 1:
            return self._discover_files_parallel(directories, max_files, existing_files, batch_check_callback, progress_callback)
        else:
            # Single path - use original sequential method
            return self._discover_files_sequential(directories, max_files, existing_files, batch_check_callback, progress_callback)
    
    def _discover_files_sequential(self, directories, max_files=None, existing_files=None, batch_check_callback=None, progress_callback=None):
        """Sequential file discovery for single path or fallback"""
        files_discovered = []
        files_count = 0
        existing_files = existing_files or set()
        total_files_checked = 0
        
        # Batch for efficient database checking
        batch_to_check = []
        BATCH_SIZE = int(os.getenv('BATCH_SIZE', '100'))
        
        for directory in directories:
            if not os.path.exists(directory):
                logger.warning(f"Directory does not exist: {directory}")
                continue
            
            files = self._get_files_sorted_by_age(directory)
            logger.info(f"Found {len(files)} total files in {directory}")
            
            for file_path in files:
                total_files_checked += 1
                
                if max_files and files_count >= max_files:
                    logger.info(f"Reached maximum discovery limit of {max_files} files")
                    # Check any remaining batch before returning
                    if batch_check_callback and batch_to_check:
                        existing_in_batch = batch_check_callback(batch_to_check)
                        for path in batch_to_check:
                            if path not in existing_in_batch and self._is_supported_file(path):
                                files_discovered.append(path)
                    return files_discovered
                
                # Use batch checking if callback provided, otherwise use legacy method
                if batch_check_callback:
                    # Add to batch for checking
                    if self._is_supported_file(file_path):
                        batch_to_check.append(file_path)
                    
                    # When batch is full, check against database
                    if len(batch_to_check) >= BATCH_SIZE:
                        existing_in_batch = batch_check_callback(batch_to_check)
                        for path in batch_to_check:
                            if path not in existing_in_batch:
                                files_discovered.append(path)
                                files_count += 1
                        batch_to_check = []
                else:
                    # Legacy method: check against in-memory set
                    if file_path in existing_files:
                        continue
                    
                    if self._is_supported_file(file_path):
                        files_discovered.append(file_path)
                        files_count += 1
                
                # Call progress callback periodically
                if progress_callback and total_files_checked % 100 == 0:
                    progress_callback(total_files_checked, files_count)
        
        # Check any remaining files in the batch
        if batch_check_callback and batch_to_check:
            existing_in_batch = batch_check_callback(batch_to_check)
            for path in batch_to_check:
                if path not in existing_in_batch:
                    files_discovered.append(path)
                    files_count += 1
        
        logger.info(f"Discovery complete: found {len(files_discovered)} new supported files")
        return files_discovered
    
    def _discover_files_parallel(self, directories, max_files=None, existing_files=None, batch_check_callback=None, progress_callback=None):
        """Parallel file discovery across multiple paths"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        
        existing_files = existing_files or set()
        all_files = []
        max_discovery_workers = min(len(directories), self.max_workers)
        
        logger.info(f"Using {max_discovery_workers} workers for parallel path discovery")
        
        # Create shared state for thread-safe file counting
        shared_state = {
            'files_count': 0,
            'max_reached': False
        }
        count_lock = threading.Lock()
        
        def discover_path(directory):
            """Discover files in a single directory"""
            if not os.path.exists(directory):
                logger.warning(f"Directory does not exist: {directory}")
                return []
            
            try:
                files = self._get_files_sorted_by_age(directory)
                logger.info(f"Found {len(files)} total files in {directory}")
                
                path_files = []
                batch_to_check = []
                BATCH_SIZE = int(os.getenv('BATCH_SIZE', '100'))
                
                for file_path in files:
                    # Check global file limit across all paths
                    with count_lock:
                        if max_files and shared_state['files_count'] >= max_files:
                            shared_state['max_reached'] = True
                            logger.info(f"Reached maximum discovery limit of {max_files} files")
                            break
                    
                    if batch_check_callback:
                        # Use batch checking for efficiency
                        if self._is_supported_file(file_path):
                            batch_to_check.append(file_path)
                        
                        # When batch is full, check against database
                        if len(batch_to_check) >= BATCH_SIZE:
                            existing_in_batch = batch_check_callback(batch_to_check)
                            for path in batch_to_check:
                                if path not in existing_in_batch:
                                    with count_lock:
                                        if max_files and shared_state['files_count'] >= max_files:
                                            shared_state['max_reached'] = True
                                            break
                                        path_files.append(path)
                                        shared_state['files_count'] += 1
                            batch_to_check = []
                    else:
                        # Legacy method: check against in-memory set
                        if file_path in existing_files:
                            continue
                        
                        if self._is_supported_file(file_path):
                            with count_lock:
                                if max_files and shared_state['files_count'] >= max_files:
                                    shared_state['max_reached'] = True
                                    break
                                path_files.append(file_path)
                                shared_state['files_count'] += 1
                
                # Check any remaining files in the batch
                if batch_check_callback and batch_to_check:
                    existing_in_batch = batch_check_callback(batch_to_check)
                    for path in batch_to_check:
                        if path not in existing_in_batch:
                            with count_lock:
                                if not (max_files and shared_state['files_count'] >= max_files):
                                    path_files.append(path)
                                    shared_state['files_count'] += 1
                
                logger.info(f"Path {directory}: discovered {len(path_files)} new supported files")
                return path_files
                
            except Exception as e:
                logger.error(f"Error discovering files in {directory}: {str(e)}")
                return []
        
        # Execute discovery in parallel
        with ThreadPoolExecutor(max_workers=max_discovery_workers) as executor:
            # Submit all path discovery tasks
            future_to_path = {executor.submit(discover_path, directory): directory 
                            for directory in directories}
            
            # Collect results as they complete
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    path_files = future.result()
                    all_files.extend(path_files)
                    
                    # Check if we've reached the maximum files limit
                    if max_files and len(all_files) >= max_files:
                        logger.info(f"Reached maximum discovery limit of {max_files} files")
                        # Cancel remaining tasks
                        for remaining_future in future_to_path:
                            if not remaining_future.done():
                                remaining_future.cancel()
                        break
                        
                except Exception as e:
                    logger.error(f"Error in path discovery for {path}: {str(e)}")
        
        # Sort all discovered files by creation time (oldest first)
        all_files.sort(key=lambda x: os.path.getctime(x))
        
        # Apply max_files limit if needed
        if max_files and len(all_files) > max_files:
            all_files = all_files[:max_files]
        
        logger.info(f"Parallel discovery complete: found {len(all_files)} new supported files across {len(directories)} paths")
        return all_files
    
    def scan_directories(self, directories, max_files=None, skip_paths=None):
        """Legacy method for backward compatibility - now uses new two-phase approach"""
        discovered_files = self.discover_media_files(directories, max_files)
        results = []
        
        skip_paths = skip_paths or set()
        
        for file_path in discovered_files:
            if file_path not in skip_paths:
                logger.info(f"Scanning new file: {file_path}")
                result = self.scan_file(file_path)
                results.append(result)
        
        return results
    
    def _get_files_sorted_by_age(self, directory):
        """Optimized file discovery using os.scandir for better performance"""
        files = []
        
        # Use os.scandir for faster directory traversal
        def scan_directory(path):
            try:
                with os.scandir(path) as entries:
                    for entry in entries:
                        full_path = entry.path
                        
                        if entry.is_dir(follow_symlinks=False):
                            # Skip excluded directories
                            if not any(is_path_under(full_path, exc) for exc in self.excluded_paths):
                                # Recursively scan subdirectory
                                scan_directory(full_path)
                        elif entry.is_file(follow_symlinks=False):
                            # Check if file extension is supported
                            extension = os.path.splitext(entry.name)[1].lower()
                            if extension in self.supported_formats and extension not in self.excluded_extensions:
                                # Check if filename matches exclusion patterns
                                import fnmatch
                                skip_file = False
                                for pattern in self.excluded_patterns:
                                    if fnmatch.fnmatch(entry.name, pattern):
                                        logger.debug(f"Skipping {entry.name} - matches exclusion pattern {pattern}")
                                        skip_file = True
                                        break
                                
                                if not skip_file:
                                    try:
                                        # Use DirEntry.stat() for better performance
                                        stat = entry.stat(follow_symlinks=False)
                                        files.append((full_path, stat.st_ctime))
                                    except OSError:
                                        # If stat fails, skip this file
                                        continue
            except (OSError, PermissionError) as e:
                logger.warning(f"Cannot access directory {path}: {e}")
        
        # Start scanning from root directory
        scan_directory(directory)
        
        # Sort by creation time (already have the ctime from stat)
        files.sort(key=lambda x: x[1])
        
        # Return just the file paths
        return [f[0] for f in files]
    
    def _is_supported_file(self, file_path):
        extension = Path(file_path).suffix.lower()
        filename = os.path.basename(file_path)
        
        # Check if extension is excluded
        if extension in self.excluded_extensions:
            return False
            
        # Check if path is excluded
        for excluded_path in self.excluded_paths:
            if is_path_under(file_path, excluded_path):
                return False
        
        # Check if filename matches exclusion patterns
        import fnmatch
        for pattern in self.excluded_patterns:
            if fnmatch.fnmatch(filename, pattern):
                logger.debug(f"Excluding {filename} - matches pattern {pattern}")
                return False
                
        return extension in self.supported_formats
    
    def get_file_info(self, file_path, timeout=None):
        """Get basic file information without scanning for corruption

        Raises FileReadTimeoutError if stat/magic stall (issue #70); other
        errors keep returning the fallback dict.
        """
        if timeout is None:
            timeout = _setting('timeouts.file_read_timeout_secs')
        try:
            def read_info():
                stats = os.stat(file_path)
                return stats, magic.from_file(file_path, mime=True)

            file_stats, file_type = _read_with_timeout(
                read_info, timeout, file_path, 'stat/magic')
            file_size = file_stats.st_size
            # UTC-aware: bitrot classification compares this stored baseline
            # against a UTC mtime, and naive local values poison it (the old
            # naive form is why mtime_baseline_utc exists).
            creation_date = datetime.fromtimestamp(file_stats.st_ctime, timezone.utc)
            last_modified = datetime.fromtimestamp(file_stats.st_mtime, timezone.utc)

            return {
                'file_path': file_path,
                'file_size': file_size,
                'file_type': file_type,
                'creation_date': creation_date,
                'last_modified': last_modified,
                # Allocated blocks, for the data integrity check. Carried here
                # so nothing downstream has to stat the file a second time.
                'file_blocks': getattr(file_stats, 'st_blocks', 0)
            }
        except FileReadTimeoutError:
            raise
        except Exception as e:
            logger.error(f"Error getting file info for {file_path}: {str(e)}")
            return {
                'file_path': file_path,
                'file_size': 0,
                'file_type': 'unknown',
                'creation_date': datetime.now(timezone.utc),
                'last_modified': datetime.now(timezone.utc)
            }
    
    def _check_data_holes(self, file_path, file_size, file_blocks):
        """Detect files that were allocated at full size but never fully written.

        An interrupted download or copy leaves the file the right length with
        unwritten regions inside it. Demuxers skip past those regions, so the
        picture holds while the clock runs, which freeze detection then reports
        as a frozen picture rather than as the missing data it is.

        The allocated-blocks ratio is only a gate deciding which files are
        worth opening, because compression and dedup under-allocate healthy
        files too. The verdict comes from SEEK_HOLE, which reports regions the
        filesystem never allocated. That distinction matters: a valid file can
        legitimately contain long runs of zero bytes (digital silence in PCM
        audio, flat colour in an uncompressed image), and those bytes were
        written. Only a real hole means data is absent.

        Takes the size and block count from the caller's existing stat. Returns
        (is_incomplete, corruption_details, scan_output).
        """
        if file_size < DATA_HOLE_MIN_SIZE or not file_blocks or file_blocks <= 0:
            # Too small to carry a meaningful hole, or a filesystem that does
            # not report allocation - either way there is nothing to compare.
            return False, [], []

        alloc_ratio = (file_blocks * 512) / file_size
        if alloc_ratio >= _setting('detection.data_hole_alloc_ratio'):
            return False, [], []

        hole_bytes, hole_runs, supported = self._measure_sparse_holes(file_path, file_size)
        size_mb = file_size / (1024 * 1024)
        scan_output = [
            "=== Data Integrity Check ===",
            f"Allocated {alloc_ratio * 100:.1f}% of {size_mb:.1f} MB nominal size",
        ]

        if not supported:
            scan_output.append(
                "Filesystem does not report sparse regions - no conclusion drawn")
            scan_output.append("=== Data Integrity Check Complete ===")
            return False, [], scan_output

        hole_pct = hole_bytes * 100.0 / file_size
        scan_output.append(
            f"Sparse regions: {hole_runs} run(s), {hole_bytes / (1024 * 1024):.1f} MB "
            f"({hole_pct:.1f}% of the file) never written")

        if hole_pct < _setting('detection.data_hole_min_pct'):
            # Under-allocated with every byte written: compression or dedup.
            scan_output.append(
                "Under-allocation is filesystem compression, not missing data")
            scan_output.append("=== Data Integrity Check Complete ===")
            return False, [], scan_output

        summary = (
            f"Incomplete file: {hole_runs} unwritten region(s) totalling "
            f"{hole_bytes / (1024 * 1024):.1f} MB, {hole_pct:.1f}% of its "
            f"{size_mb:.1f} MB length"
        )
        scan_output.append(summary)
        scan_output.append("=== Data Integrity Check Complete ===")
        logger.warning(f"Incomplete file detected: {file_path} - {summary}")
        return True, [summary], scan_output

    def _measure_sparse_holes(self, file_path, size):
        """Total the file's unwritten regions using SEEK_HOLE / SEEK_DATA.

        Costs a handful of seeks and reads no data. Returns
        (hole_bytes, hole_runs, supported). A filesystem without sparse-region
        support reports the whole file as data, which is indistinguishable from
        a healthy file, so it is reported as unsupported rather than as clean.
        """
        def scan():
            fd = os.open(file_path, os.O_RDONLY)
            try:
                # A filesystem without sparse-region support rejects the whence
                # itself rather than reporting a fully-written file, so the
                # error is what separates "cannot tell" from "no holes".
                try:
                    os.lseek(fd, 0, os.SEEK_HOLE)
                except OSError as e:
                    if e.errno in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                        return 0, 0, False
                    raise
                hole_bytes = 0
                hole_runs = 0
                offset = 0
                while offset < size:
                    hole_start = os.lseek(fd, offset, os.SEEK_HOLE)
                    if hole_start >= size:
                        break
                    try:
                        hole_end = os.lseek(fd, hole_start, os.SEEK_DATA)
                    except OSError:
                        hole_end = size  # no further data: hole runs to EOF
                    hole_bytes += hole_end - hole_start
                    hole_runs += 1
                    offset = hole_end
                return hole_bytes, hole_runs, True
            finally:
                os.close(fd)

        try:
            return _read_with_timeout(scan, _setting('timeouts.file_read_timeout_secs'), file_path, 'sparse scan')
        except FileReadTimeoutError:
            # A stalled mount is not evidence that data is missing.
            logger.warning(f"Sparse-region scan stalled for {file_path}; drawing no conclusion")
            return 0, 0, False
        except (OSError, ValueError) as e:
            logger.debug(f"Sparse-region scan failed for {file_path}: {e}")
            return 0, 0, False

    def calculate_file_hash(self, file_path, timeout=None, file_size=None):
        """Calculate SHA-256 hash of a file with optimized chunk size

        Raises FileReadTimeoutError if the read stalls past the deadline
        (issue #70); all other errors keep returning None. Pass file_size
        when already known to skip a redundant guarded stat.
        """
        try:
            if file_size is None:
                file_size = _read_with_timeout(
                    lambda: os.path.getsize(file_path),
                    timeout if timeout is not None else _setting('timeouts.file_read_timeout_secs'),
                    file_path, 'stat')
            if timeout is None:
                # Deadline assumes storage sustains at least ~5MB/s
                timeout = _setting('timeouts.file_read_timeout_secs') + int(file_size / (5 * 1024 * 1024))
            return _read_with_timeout(
                lambda: self._hash_file_contents(file_path, file_size),
                timeout, file_path, 'hash read')
        except FileReadTimeoutError:
            raise
        except Exception as e:
            logger.error(f"Error calculating hash for {file_path}: {str(e)}")
            return None

    def _hash_file_contents(self, file_path, file_size):
        """Blocking hash read; callers bound it via _read_with_timeout."""
        logger.info(f"Calculating hash for: {file_path}")
        hash_sha256 = hashlib.sha256()
        start_time = time.time()
        bytes_processed = 0

        # NEVER skip hash - integrity checking is critical for all files
        # Use mmap for files > 100MB (5-10x faster), adaptive buffering for smaller files
        if file_size > 100 * 1024 * 1024:  # 100MB threshold
            # Use memory-mapped I/O for large files (5-10x faster)
            logger.info(f"Hashing large file ({file_size/1024/1024/1024:.1f}GB) with mmap: {file_path}")
            try:
                with open(file_path, "rb") as f:
                    # Map entire file into memory (OS handles paging)
                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                        hash_sha256.update(mm)
                        bytes_processed = file_size
            except (OSError, ValueError) as e:
                # Fallback to buffered read if mmap fails
                logger.warning(f"mmap failed for {file_path}, falling back to buffered read: {e}")
                with open(file_path, "rb") as f:
                    chunk_size = 16 * 1024 * 1024  # 16MB chunks
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        hash_sha256.update(chunk)
                        bytes_processed += len(chunk)
        else:
            # Use adaptive chunk sizes for smaller files
            chunk_size = 1024 * 1024  # 1MB chunks for files up to 1GB

            # For files 1-10GB, use larger chunks
            if file_size > 1024 * 1024 * 1024:
                chunk_size = 4 * 1024 * 1024  # 4MB chunks

            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    hash_sha256.update(chunk)
                    bytes_processed += len(chunk)

                    # Log progress for large files every 100MB
                    elapsed = time.time() - start_time
                    if bytes_processed % (100 * 1024 * 1024) == 0:
                        mb_processed = bytes_processed / (1024 * 1024)
                        mb_per_sec = mb_processed / elapsed if elapsed > 0 else 0
                        logger.info(f"Hash progress for {file_path}: {mb_processed:.0f}MB processed in {elapsed:.1f}s ({mb_per_sec:.1f}MB/s)")

        total_time = time.time() - start_time
        if total_time > 10:  # Log completion time for files that take more than 10 seconds
            mb_size = bytes_processed / (1024 * 1024)
            mb_per_sec = mb_size / total_time if total_time > 0 else 0
            logger.info(f"Hash complete for {file_path}: {mb_size:.1f}MB in {total_time:.1f}s ({mb_per_sec:.1f}MB/s)")

        return hash_sha256.hexdigest()
    
    def scan_files_parallel(self, file_paths, progress_callback=None, scan_paths=None, force_rescan=False):
        """Scan multiple files in parallel using ThreadPoolExecutor with path-based optimization"""
        
        # If scan_paths provided and multiple paths, use path-based parallel scanning
        if scan_paths and len(scan_paths) > 1:
            return self._scan_files_by_paths_parallel(file_paths, progress_callback, scan_paths, force_rescan)
        else:
            # Use original single-pool approach
            return self._scan_files_single_pool(file_paths, progress_callback, force_rescan)
    
    def _scan_files_single_pool(self, file_paths, progress_callback=None, force_rescan=False):
        """Original single thread pool scanning approach"""
        results = []
        completed = 0
        total = len(file_paths)
        
        logger.info(f"Starting parallel scan of {total} files with {self.max_workers} workers")
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_file = {
                executor.submit(self.scan_file, file_path, force_rescan): file_path 
                for file_path in file_paths
            }
            
            # Process completed tasks
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                try:
                    result = future.result()
                    results.append(result)
                    completed += 1
                    
                    # Update progress callback if provided
                    if progress_callback:
                        progress_callback(completed, total, file_path)
                        
                except Exception as e:
                    logger.error(f"Error scanning {file_path}: {str(e)}")
                    results.append({
                        'file_path': file_path,
                        'file_size': 0,
                        'file_type': 'unknown',
                        'creation_date': datetime.now(timezone.utc),
                        # No real file mtime available here; None keeps the
                        # stored baseline instead of poisoning it with scan time
                        'last_modified': None,
                        'is_corrupted': True,
                        'corruption_details': f"Scan error: {str(e)}"
                    })
                    completed += 1
                    
                    if progress_callback:
                        progress_callback(completed, total, file_path)
        
        logger.info(f"Parallel scan completed: {completed}/{total} files processed")
        return results
    
    def _scan_files_by_paths_parallel(self, file_paths, progress_callback=None, scan_paths=None, force_rescan=False):
        """Scan files using dedicated worker pools per path"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        
        # Organize files by their base path
        files_by_path = {}
        for file_path in file_paths:
            # Find which scan path this file belongs to
            base_path = None
            for scan_path in scan_paths:
                if file_path.startswith(scan_path):
                    base_path = scan_path
                    break
            
            if base_path:
                if base_path not in files_by_path:
                    files_by_path[base_path] = []
                files_by_path[base_path].append(file_path)
        
        # Calculate workers per path
        num_paths = len(files_by_path)
        workers_per_path = max(1, self.max_workers // num_paths)
        
        logger.info(f"Starting path-based parallel scanning: {num_paths} paths with {workers_per_path} workers each")
        
        # Shared state for progress tracking
        shared_state = {
            'completed': 0,
            'total': len(file_paths),
            'results': []
        }
        progress_lock = threading.Lock()
        
        def scan_path_files(path, path_files):
            """Scan all files in a specific path"""
            path_results = []
            logger.info(f"Starting scan of {len(path_files)} files in path: {path}")
            
            with ThreadPoolExecutor(max_workers=workers_per_path) as executor:
                # Submit all files in this path
                future_to_file = {
                    executor.submit(self.scan_file, file_path, force_rescan): file_path 
                    for file_path in path_files
                }
                
                # Process completed files
                for future in as_completed(future_to_file):
                    file_path = future_to_file[future]
                    try:
                        result = future.result()
                        path_results.append(result)
                        
                        # Update shared progress
                        with progress_lock:
                            shared_state['completed'] += 1
                            if progress_callback:
                                progress_callback(shared_state['completed'], shared_state['total'], file_path)
                        
                    except Exception as e:
                        logger.error(f"Error scanning {file_path}: {str(e)}")
                        error_result = {
                            'file_path': file_path,
                            'file_size': 0,
                            'file_type': 'unknown',
                            'creation_date': datetime.now(timezone.utc),
                            # No real file mtime available here; None keeps the
                            # stored baseline instead of poisoning it with scan time
                            'last_modified': None,
                            'is_corrupted': True,
                            'corruption_details': f"Scan error: {str(e)}"
                        }
                        path_results.append(error_result)
                        
                        # Update shared progress
                        with progress_lock:
                            shared_state['completed'] += 1
                            if progress_callback:
                                progress_callback(shared_state['completed'], shared_state['total'], file_path)
            
            logger.info(f"Path scan completed for {path}: {len(path_results)} files processed")
            return path_results
        
        # Execute path scanning in parallel
        with ThreadPoolExecutor(max_workers=num_paths) as path_executor:
            # Submit path scanning tasks
            future_to_path = {
                path_executor.submit(scan_path_files, path, files): path 
                for path, files in files_by_path.items()
            }
            
            # Collect results from all paths
            all_results = []
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    path_results = future.result()
                    all_results.extend(path_results)
                except Exception as e:
                    logger.error(f"Error in path scanning for {path}: {str(e)}")
        
        logger.info(f"Path-based parallel scan completed: {len(all_results)} files processed across {num_paths} paths")
        return all_results
    
    def scan_file(self, file_path, force_rescan=False):
        """Scan a single file for corruption
        
        Args:
            file_path (str): Path to the file to scan
            force_rescan (bool): If True, rescan the file even if already in cache
        """
        scan_start_time = time.time()
        scan_tool = None
        scan_output = []
        
        try:
            # Only log every 100th file to reduce logging overhead
            if hasattr(self, '_scan_count'):
                self._scan_count += 1
            else:
                self._scan_count = 1
                
            if self._scan_count % 100 == 0:
                logger.info(f"Scanning file #{self._scan_count}: {file_path}")
            
            # Update current scan tracking
            with self.scan_lock:
                self.current_scan_file = file_path
                self.scan_start_time = scan_start_time
            
            # Get basic file info first
            file_info = self.get_file_info(file_path)

            # Calculate file hash (reuse the stat from file_info; its fallback
            # dict reports size 0, in which case hash re-stats under its guard)
            file_hash = self.calculate_file_hash(
                file_path, file_size=file_info['file_size'] or None)
            
            # Check cache if not forcing rescan
            if not force_rescan and self.database_path:
                cached_result = self._check_cache(file_path, file_hash, file_info['last_modified'])
                if cached_result:
                    logger.info(f"Using cached result for {file_path}")
                    return cached_result
            
            is_corrupted = False
            corruption_details = []
            warning_details = []
            
            extension = Path(file_path).suffix.lower()
            # Unwritten regions are checked before any decode. A file with holes
            # in it is missing data, and that verdict does not depend on what the
            # decoder makes of what is left - running a full decode over it only
            # produces freeze and frame-count noise that describes the holes.
            holes_found = False
            if extension in self.supported_formats:
                holes_found, hole_details, hole_output = self._check_data_holes(
                    file_path, file_info['file_size'], file_info.get('file_blocks', 0))
                # Kept even when the file is cleared: an operator looking at a
                # suspiciously under-allocated file needs the reason it passed.
                scan_output.extend(hole_output)
            if holes_found:
                corruption_details.extend(hole_details)
                is_corrupted = True
                scan_tool = "data-integrity"
            elif extension in self.supported_image_formats:
                is_corrupted, details, tool, output, warnings = self._check_image_corruption(file_path)
                corruption_details.extend(details)
                scan_tool = tool
                scan_output.extend(output)
                warning_details = warnings
            elif extension in self.supported_video_formats:
                is_corrupted, details, tool, output, warnings = self._check_video_corruption(file_path)
                corruption_details.extend(details)
                scan_tool = tool
                scan_output.extend(output)
                warning_details = warnings
            elif extension in self.supported_audio_formats:
                is_corrupted, details, tool, output, warnings = self._check_audio_corruption(file_path)
                corruption_details.extend(details)
                scan_tool = tool
                scan_output.extend(output)
                warning_details = warnings
            else:
                # File type not supported for detailed scanning
                scan_tool = "unsupported"
                scan_output.append(f"File type {extension} not supported for corruption checking")
                corruption_details.append(f"File type {extension} not supported for corruption checking")
                is_corrupted = False  # Consider unsupported files as not corrupted
            
            scan_duration = time.time() - scan_start_time
            
            # Only log corrupted files and periodic status updates
            if is_corrupted:
                status = "CORRUPTED"
                logger.warning(f"CORRUPTED file found: {file_path} - {'; '.join(corruption_details)}")
            elif self._scan_count % 100 == 0:
                logger.info(f"Scan progress: {self._scan_count} files scanned")
            
            # Merge file info with scan results
            result = file_info.copy()
            result.update({
                'is_corrupted': is_corrupted,
                'corruption_details': '; '.join(corruption_details) if corruption_details else None,
                'file_hash': file_hash,
                'scan_tool': scan_tool,
                'scan_duration': scan_duration,
                'scan_output': '\n'.join(scan_output) if scan_output else None,
                'has_warnings': len(warning_details) > 0,
                'warning_details': '; '.join(warning_details) if warning_details else None
            })
            
            # Save to cache
            self._save_to_cache(file_path, result)
            
            return result
        
        except Exception as e:
            scan_duration = time.time() - scan_start_time
            logger.error(f"Error scanning file {file_path}: {str(e)}")
            result = {
                'file_path': file_path,
                'file_size': 0,
                'file_type': 'unknown',
                'creation_date': datetime.now(timezone.utc),
                'last_modified': datetime.now(timezone.utc),
                'is_corrupted': True,
                'corruption_details': f"Scan error: {str(e)}",
                'file_hash': None,
                'scan_tool': 'error',
                'scan_duration': scan_duration,
                'scan_output': str(e),
                'has_warnings': False,
                'warning_details': None
            }
            # Persist the failure: without this the row stays 'pending' in the
            # non-chunked paths and an unreadable file is re-selected forever
            # (the re-stick loop of issue #70). No-op when database_path unset.
            self._save_to_cache(file_path, result)
            return result
        finally:
            # Clear current scan tracking
            with self.scan_lock:
                self.current_scan_file = None
                self.scan_start_time = None
    
    def _check_image_corruption(self, file_path):
        corruption_details = []
        is_corrupted = False
        scan_tool = "pil"
        scan_output = []
        warning_details = []
        
        logger.info(f"Starting PIL verification for: {file_path}")
        
        # Check file type
        file_ext = os.path.splitext(file_path)[1].lower()
        is_gif = file_ext == '.gif'
        is_heic = file_ext in ['.heic', '.heif']
        is_jpeg = file_ext in ['.jpg', '.jpeg']
        
        # Check if HEIC is supported
        if is_heic and not HEIF_SUPPORT:
            scan_output.append("PIL verification: SKIPPED (HEIC support not available)")
            logger.info(f"PIL HEIC support not available, skipping PIL verification for {file_path}")
            pil_failed = False
            pil_error = None
        else:
            pil_failed = False
            pil_error = None
            
            try:
                with Image.open(file_path) as img:
                    img.verify()
                logger.info(f"PIL verification passed for: {file_path}")
                scan_output.append("PIL verification: PASSED")
            except Exception as e:
                pil_failed = True
                pil_error = str(e)
                scan_output.append(f"PIL verification: FAILED - {str(e)}")
                logger.warning(f"PIL verification failed for {file_path}: {str(e)}")
        
        pil_load_failed = False
        pil_load_error = None
        pil_size_limited = False  # Pillow bomb guard fired: a size limit, not a PIL failure

        # File size needed for JPEG pixel analysis guard and ImageMagick timeout
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            file_size = 0
        file_size_mb = file_size / (1024 * 1024)

        # Skip load test for HEIC if not supported
        if is_heic and not HEIF_SUPPORT:
            scan_output.append("PIL load test: SKIPPED (HEIC support not available)")
        else:
            try:
                with Image.open(file_path) as img:
                    img.load()
                
                if img.size[0] == 0 or img.size[1] == 0:
                    corruption_details.append("Invalid image dimensions")
                    is_corrupted = True
                    scan_output.append(f"Image dimensions: {img.size[0]}x{img.size[1]} (INVALID)")
                else:
                    scan_output.append(f"Image dimensions: {img.size[0]}x{img.size[1]}")
                
                    # Note: After load(), tile data is consumed and cleared in PIL - this is normal behavior
                    # Removed incorrect tile data check that was causing false positives
                    img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                    scan_output.append("Transform test: PASSED")

                # JPEG pixel analysis -- reuses already-loaded image, no extra file open
                if is_jpeg:
                    if file_size_mb <= 10:
                        jpeg_corrupted, jpeg_details, jpeg_output = self._check_jpeg_pixel_corruption(img)
                        if jpeg_corrupted:
                            is_corrupted = True
                            corruption_details.extend(jpeg_details)
                            scan_output.extend(jpeg_output)
                            logger.warning(f"JPEG pixel corruption in {file_path}")
                    else:
                        scan_output.append(f"JPEG pixel analysis: SKIPPED (file too large: {file_size_mb:.1f}MB)")

            except Exception as e:
                pil_load_failed = True
                pil_load_error = str(e)
                error_lower = str(e).lower()
                scan_output.append(f"PIL load/transform: FAILED - {str(e)}")

                # Check for truncation errors - these indicate actual corruption
                if 'truncated' in error_lower or 'bytes not processed' in error_lower:
                    logger.warning(f"Image truncation detected for {file_path}: {str(e)}")
                    corruption_details.append(f"Image file is truncated: {str(e)}")
                    is_corrupted = True
                    scan_tool = "pil"
                # HEIC files with "cannot identify" may be due to libheif limitations, not corruption
                # Don't mark as corrupted here - let ImageMagick verify (which will also catch the libheif error)
                elif is_heic and 'cannot identify image file' in error_lower:
                    logger.info(f"HEIC PIL load failed (may be libheif limitation) for {file_path}: {str(e)}")
                    # Don't mark as corrupted yet - ImageMagick will provide the definitive answer
                # Pillow's decompression-bomb guard is a pixel-count limit, not corruption evidence
                elif isinstance(e, Image.DecompressionBombError):
                    pil_size_limited = True
                    warning_details.append("Image exceeds Pillow pixel-count limit; PIL validation skipped (file likely valid)")
                    scan_output.append("PIL load test: SKIPPED (exceeds pixel-count limit)")
                    logger.info(f"Pillow decompression-bomb guard for {file_path}: {str(e)}")
        
        if pil_size_limited:
            # The shipped image's ImageMagick policy (256MP area) is below
            # Pillow's bomb threshold (~358MP), so the convert can only grind
            # its pixel cache and fail at a resource limit; the warning is
            # already recorded, so skip the subprocess outright
            scan_output.append("ImageMagick convert: SKIPPED (image exceeds pixel-count limits)")
            logger.info(f"Skipping ImageMagick for {file_path}: exceeds Pillow pixel-count limit")
            return is_corrupted, corruption_details, "pil", scan_output, warning_details

        logger.info(f"Starting ImageMagick verification for: {file_path}")
        
        # Scale timeout with file size, no artificial limit for large files
        if is_gif:
            # GIFs can be complex with many frames - scale timeout with file size
            imagemagick_timeout = 120 + int(file_size_mb / 5)  # 120s base + 1s per 5MB, no max
        else:
            # Regular images - scale timeout with file size
            imagemagick_timeout = 60 + int(file_size_mb / 10)   # 60s base + 1s per 10MB, no max
        
        logger.info(f"ImageMagick timeout set to {imagemagick_timeout}s for {file_size_mb:.1f}MB {file_ext.upper()} file")
        
        try:
            # Enhanced ImageMagick validation with comprehensive checks
            # Using 'convert' instead of 'identify' to validate PIXEL DATA, not just headers
            # This forces ImageMagick to decode the entire image, detecting deeper corruption
            result = safe_subprocess_run(
                [IMAGEMAGICK_BINARY,
                 ensure_cli_safe_path(file_path),  # no '--' separator support; './'-prefix relative paths
                 '-regard-warnings',      # Treat warnings as errors for strict validation
                 'null:'],                # Null output (discard result, we only check for errors)
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',  # Replace undecodable bytes with � character
                timeout=imagemagick_timeout
            )
            
            if result.returncode != 0:
                scan_output.append(f"ImageMagick convert: FAILED (exit code {result.returncode})")
                if result.stderr:
                    scan_output.append(f"ImageMagick stderr: {result.stderr[:200]}")
                    stderr_lower = result.stderr.lower()
                    # Check for GIF header errors specifically
                    if is_gif and 'improper image header' in stderr_lower and 'readgifimage' in stderr_lower:
                        # This is a common false positive for GIFs that still work
                        logger.info(f"GIF header warning (not corruption) for {file_path}")
                    # Check for HEIC/HEIF libheif limitation errors (iOS 18 files with shared auxiliary images)
                    # These are NOT corruption - older libheif versions don't support newer HEIC features
                    # See: https://github.com/strukturag/libheif/issues/1190
                    elif is_heic and ('auxiliary image' in stderr_lower or 'too many auxiliary' in stderr_lower):
                        warning_details.append("HEIC validation skipped: libheif version limitation (file likely valid)")
                        scan_output.append("ImageMagick HEIC: SKIPPED (libheif limitation - not corruption)")
                        scan_output.append(f"Note: iOS 18+ HEIC files may use features not supported by older libheif versions")
                        logger.info(f"HEIC libheif limitation (not corruption) for {file_path}: {result.stderr[:100]}")
                        # Don't mark as corrupted - this is a tool limitation, not file corruption
                    # ImageMagick resource/policy limits (cache resources exhausted, width/height/area
                    # exceeds user limit) are tool limits on oversized-but-valid images, not corruption
                    elif 'cache resources exhausted' in stderr_lower or 'exceeds user limit' in stderr_lower:
                        warning_details.append("Image validation skipped: exceeds ImageMagick resource limit (file likely valid)")
                        scan_output.append("ImageMagick convert: SKIPPED (resource limit - not corruption)")
                        logger.info(f"ImageMagick resource limit (not corruption) for {file_path}: {result.stderr[:100]}")
                    else:
                        corruption_details.append("ImageMagick pixel validation failed")
                        is_corrupted = True
                        scan_tool = "imagemagick"
                else:
                    # Check if PIL passed before marking as corrupted
                    # ImageMagick might fail due to missing delegates/decoders
                    if pil_size_limited or (not pil_failed and not pil_load_failed):
                        # PIL passed, so file is likely OK - ImageMagick issue
                        warning_details.append("ImageMagick convert failed (but PIL passed - likely decoder issue)")
                        scan_output.append("Note: ImageMagick failed but PIL verified OK")
                        scan_tool = "pil"  # Use PIL as the authoritative tool
                    else:
                        # Both PIL and ImageMagick failed - likely corrupted
                        corruption_details.append("ImageMagick pixel validation failed")
                        is_corrupted = True
                        scan_tool = "imagemagick"
                logger.warning(f"ImageMagick convert failed for {file_path}")
            elif result.stderr:
                # Check if this is just a metadata/profile warning (not actual corruption)
                stderr_lower = result.stderr.lower()
                # Note: ImageMagick emits "@ warning/profile.c" (space after @)
                is_profile_warning = 'corruptimageprofile' in stderr_lower and 'warning/profile.c' in stderr_lower
                
                # Check for PNG chunk warnings that aren't actual corruption
                png_chunk_warnings = [
                    'sbit: invalid',
                    'iccp: known incorrect srgb profile',
                    'phys chunk',
                    'text chunk',
                    'itxt chunk',
                    'time chunk',
                    'bkgd chunk'
                ]
                is_png_warning = any(warning in stderr_lower for warning in png_chunk_warnings) and 'warning/png.c' in stderr_lower
                
                if is_profile_warning:
                    # Profile warnings (like XMP) don't indicate actual image corruption
                    scan_output.append("ImageMagick convert: PASSED (with profile warnings)")
                    logger.info(f"ImageMagick profile warning (not corruption) for {file_path}: {result.stderr[:100]}")
                elif is_png_warning:
                    # PNG chunk warnings don't indicate actual image corruption
                    warning_details.append("PNG metadata warning")
                    scan_output.append("ImageMagick convert: PASSED (with PNG metadata warnings)")
                    logger.info(f"ImageMagick PNG warning (not corruption) for {file_path}: {result.stderr[:100]}")
                elif any(keyword in stderr_lower for keyword in ['error', 'corrupt', 'truncated', 'damaged']):
                    corruption_details.append(f"ImageMagick warnings: {result.stderr[:100]}")
                    is_corrupted = True
                    scan_tool = "imagemagick"
                    scan_output.append(f"ImageMagick warnings: {result.stderr[:200]}")
                    logger.warning(f"ImageMagick warnings for {file_path}: {result.stderr[:100]}")
                else:
                    scan_output.append("ImageMagick convert: PASSED (with warnings)")
            else:
                scan_output.append("ImageMagick convert: PASSED")
                logger.info(f"ImageMagick pixel validation passed for: {file_path}")
        
        except subprocess.TimeoutExpired:
            # ImageMagick timeout should be a warning, not corruption
            # Only mark as corrupted if other tools also failed
            timeout_msg = f"ImageMagick convert timeout ({imagemagick_timeout}s) - file may be very complex"
            
            # If PIL passed (or only hit its size guard), treat timeout as
            # warning rather than corruption - a huge valid image can outlast
            # the size-scaled timeout before hitting ImageMagick's cache limit
            if pil_size_limited or (not pil_failed and not pil_load_failed):
                warning_details.append(timeout_msg)
                scan_output.append("ImageMagick identify: TIMEOUT (treating as warning - PIL verification passed)")
                logger.warning(f"ImageMagick timeout for {file_path} - treating as warning since PIL passed")
            else:
                # PIL also failed, more likely to be actual corruption
                corruption_details.append("ImageMagick identify timeout")
                is_corrupted = True
                scan_tool = "imagemagick"
                scan_output.append("ImageMagick identify: TIMEOUT (corruption likely - PIL also failed)")
                logger.warning(f"ImageMagick timeout for {file_path} - marking as corrupted since PIL also failed")
        except FileNotFoundError:
            scan_output.append("ImageMagick: NOT FOUND")
            logger.warning("ImageMagick not found, skipping advanced image checks")
        except Exception as e:
            corruption_details.append(f"ImageMagick error: {str(e)}")
            is_corrupted = True
            scan_tool = "imagemagick"
            scan_output.append(f"ImageMagick error: {str(e)}")
            logger.warning(f"ImageMagick error for {file_path}: {str(e)}")

        # P2 FIX: Removed FFmpeg image validation (lines 1114-1211)
        # FFmpeg is designed for video/audio, not images
        # Using FFmpeg for image validation caused false positives
        # PIL and ImageMagick provide proper image validation

        # Check if this is a GIF with header issues that should be a warning instead
        if is_gif and is_corrupted:
            # Check if all failures are related to "cannot identify" or "improper header"
            gif_header_issue = False
            
            if pil_failed and pil_error and 'cannot identify image file' in pil_error:
                gif_header_issue = True
            
            if any('improper image header' in detail.lower() for detail in corruption_details):
                gif_header_issue = True
            
            # If FFmpeg passed but PIL/ImageMagick failed, it's likely a false positive
            ffmpeg_passed = any('FFmpeg image validation: PASSED' in line for line in scan_output)
            
            if gif_header_issue and (ffmpeg_passed or (pil_failed and not pil_load_failed)):
                # Convert to warning instead of corruption
                logger.info(f"Converting GIF header errors to warnings for {file_path}")
                is_corrupted = False
                warning_details = ["GIF header warning: Non-standard header detected (file may still be playable)"]
                # Clear corruption details since we're treating it as a warning
                corruption_details = []
        
        # Final reconciliation: If PIL passed but other tools failed, trust PIL
        # PIL is the most reliable for basic image integrity
        if not pil_failed and not pil_load_failed and is_corrupted:
            # PIL verified the image successfully
            pil_passed_msg = "PIL verification and load test passed"
            
            # Check if corruption was only due to ImageMagick/FFmpeg failures
            if all('ImageMagick' in detail or 'FFmpeg' in detail for detail in corruption_details):
                logger.info(f"Overriding corruption status for {file_path} - PIL passed, other tools may have decoder issues")
                is_corrupted = False
                # Move corruption details to warnings
                warning_details.extend(corruption_details)
                warning_details.append("File verified OK by PIL - other tool failures likely due to decoder/configuration issues")
                corruption_details = []
                scan_tool = "pil"  # PIL is authoritative
        
        # Check if this is a HEIC/HEIF with compatibility issues that should be warnings
        if is_heic and is_corrupted:
            # Check if FFmpeg had compatibility issues
            ffmpeg_heic_issue = any('SKIPPED (HEIC compatibility)' in line for line in scan_output)
            
            # Check if PIL couldn't handle HEIC
            pil_heic_skipped = any('SKIPPED (HEIC support not available)' in line for line in scan_output)
            
            # Check if ImageMagick passed
            imagemagick_passed = any('ImageMagick identify: PASSED' in line for line in scan_output)
            
            # If FFmpeg had HEIC issues but ImageMagick passed, it's likely a false positive
            if (ffmpeg_heic_issue or pil_heic_skipped) and imagemagick_passed:
                logger.info(f"Converting HEIC compatibility errors to warnings for {file_path}")
                is_corrupted = False
                warning_details = ["HEIC compatibility warning: FFmpeg/PIL may not fully support this HEIC file (image is valid)"]
                corruption_details = []
        
        # Check if this is a WebP with EXIF issues that should be a warning instead
        is_webp = file_path.lower().endswith('.webp')
        if is_webp and is_corrupted:
            # Check if the only issue is EXIF/TIFF header warnings
            only_exif_issues = True
            
            # Check if FFmpeg only reported EXIF warnings
            ffmpeg_exif_only = any('PASSED (with EXIF warnings)' in line for line in scan_output)
            
            # Check if other tools passed
            pil_passed = not pil_failed or any('PIL verification: PASSED' in line for line in scan_output)
            imagemagick_passed = any('ImageMagick identify: PASSED' in line for line in scan_output)
            
            # If the only failures are EXIF-related, convert to warning
            if ffmpeg_exif_only or (pil_passed and imagemagick_passed and 
                any('invalid tiff header' in detail.lower() for detail in corruption_details)):
                logger.info(f"Converting WebP EXIF errors to warnings for {file_path}")
                is_corrupted = False
                warning_details = ["WebP EXIF warning: Invalid metadata detected (image displays correctly)"]
                # Clear corruption details since we're treating it as a warning
                corruption_details = []
        
        return is_corrupted, corruption_details, scan_tool, scan_output, warning_details
    
    def _check_jpeg_pixel_corruption(self, img):
        """Detect visual corruption in JPEG files via pixel analysis.

        Accepts an already-loaded PIL Image to avoid opening the file a
        second time. The caller (in _check_image_corruption) has already
        called img.load(), so pixel data is in memory.

        Returns (is_corrupted, corruption_details, scan_output).
        """
        corruption_details = []
        scan_output = []

        try:
            start_time = time.monotonic()
            width, height = img.size

            if height < 20 or width < 10:
                scan_output.append("JPEG pixel analysis: SKIPPED (image too small)")
                return False, corruption_details, scan_output

            # Guard against large images that would consume too much RAM
            total_pixels = width * height
            if total_pixels > 30_000_000:
                scan_output.append(f"JPEG pixel analysis: SKIPPED (image too large: {width}x{height})")
                return False, corruption_details, scan_output

            # Downscale to ~200px wide before pixel access. The detection algorithm
            # only needs row-averaged color data -- full resolution is wasted and
            # creates 36MB allocations per image that bypass PIL's block allocator,
            # causing memory fragmentation over thousands of files (Pillow #3610).
            sample_w = min(200, width)
            sample_h = max(1, int(height * sample_w / width))
            sampled = img.resize((sample_w, sample_h), Image.NEAREST)
            if sampled.mode != 'RGB':
                sampled = sampled.convert('RGB')
            pixels = sampled.load()

            row_averages = []
            for row_idx in range(sample_h):
                if time.monotonic() - start_time > 30:
                    scan_output.append("JPEG pixel analysis: SKIPPED (timeout)")
                    return False, corruption_details, scan_output
                r_sum, g_sum, b_sum = 0, 0, 0
                for col_idx in range(sample_w):
                    r, g, b = pixels[col_idx, row_idx]
                    r_sum += r
                    g_sum += g
                    b_sum += b
                row_averages.append((r_sum // sample_w, g_sum // sample_w, b_sum // sample_w))

            if len(row_averages) < 4:
                scan_output.append("JPEG pixel analysis: SKIPPED (insufficient rows)")
                return False, corruption_details, scan_output

            # Scan lower 80% for both signals in one pass:
            # Signal 1: Sustained chaos -- 8+ consecutive rows with jump > 100
            # Signal 2: Bottom-anchored solid fill preceded by chaos -- 30+ identical rows
            #   reaching image bottom, with chaotic rows before the fill start
            start_row = len(row_averages) // 5
            total_rows = len(row_averages)

            max_chaos_streak = 0
            current_chaos = 0
            chaos_region_start = 0
            max_chaos_start = 0

            max_fill_streak = 1
            current_fill = 1
            fill_start = 0
            fill_end = 0

            row_jumps = []

            for i in range(start_row + 1, total_rows):
                prev = row_averages[i - 1]
                curr = row_averages[i]
                jump = abs(curr[0] - prev[0]) + abs(curr[1] - prev[1]) + abs(curr[2] - prev[2])
                row_jumps.append((i, jump))

                if jump > 100:
                    if current_chaos == 0:
                        chaos_region_start = i
                    current_chaos += 1
                    if current_chaos > max_chaos_streak:
                        max_chaos_streak = current_chaos
                        max_chaos_start = chaos_region_start
                else:
                    current_chaos = 0

                if curr == prev:
                    current_fill += 1
                    if current_fill >= max_fill_streak:
                        max_fill_streak = current_fill
                        fill_start = i - current_fill + 1
                        fill_end = i
                else:
                    current_fill = 1

            has_chaos = max_chaos_streak >= 8
            near_bottom = fill_end >= total_rows * 0.95

            chaos_before_fill = False
            if max_fill_streak >= 30 and near_bottom:
                lookback_start = max(0, fill_start - start_row - 1 - 10)
                lookback_end = fill_start - start_row - 1
                chaotic_before = sum(1 for _, j in row_jumps[lookback_start:lookback_end] if j > 100)
                chaos_before_fill = chaotic_before >= 3
            has_fill = max_fill_streak >= 30 and near_bottom and chaos_before_fill

            if has_chaos or has_fill:
                details = []
                if has_chaos:
                    pct = int(max_chaos_start / total_rows * 100)
                    details.append(f"sustained chaos ({max_chaos_streak} rows) starting at {pct}%")
                if has_fill:
                    pct = int(fill_start / total_rows * 100)
                    details.append(f"solid fill streak of {max_fill_streak} rows at {pct}% to bottom")
                detail_str = "; ".join(details)
                corruption_details.append(f"JPEG pixel corruption detected: {detail_str}")
                scan_output.append(f"JPEG pixel analysis: CORRUPTED ({detail_str})")
                logger.warning(f"JPEG pixel corruption detected: {detail_str}")
                return True, corruption_details, scan_output

            scan_output.append(f"JPEG pixel analysis: PASSED (chaos={max_chaos_streak}, fill={max_fill_streak})")
            return False, corruption_details, scan_output

        except Exception as e:
            scan_output.append(f"JPEG pixel analysis: ERROR - {str(e)}")
            logger.warning(f"JPEG pixel analysis error: {e}")
            return False, corruption_details, scan_output

    def _check_video_corruption(self, file_path):
        corruption_details = []
        is_corrupted = False
        scan_tool = "ffmpeg"
        scan_output = []
        warning_details = []
        codec_name = None
        codec_profile = None
        duration = None  # Initialize duration to prevent UnboundLocalError

        logger.info(f"Starting FFmpeg probe for: {file_path}")

        # First check if file exists to avoid marking missing files as corrupted
        if not os.path.exists(file_path):
            error_msg = f"File not found: {file_path}"
            logger.warning(error_msg)
            # Return as error, not corruption
            return False, [], scan_tool, [error_msg], []

        try:
            # Enhanced probe with additional validation parameters
            # Note: ffmpeg-python probe doesn't accept boolean kwargs directly
            probe = _ffprobe_with_timeout(file_path)
            
            if 'streams' not in probe or len(probe['streams']) == 0:
                corruption_details.append("No video streams found")
                is_corrupted = True
                scan_output.append("FFmpeg probe: No streams found")
                logger.warning(f"No streams found in {file_path}")
                return is_corrupted, corruption_details, scan_tool, scan_output, warning_details
            
            video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            if not video_stream:
                corruption_details.append("No video stream found")
                is_corrupted = True
                scan_output.append("FFmpeg probe: No video stream found")
                logger.warning(f"No video stream found in {file_path}")
            else:
                codec_name = video_stream.get('codec_name', 'unknown codec')
                codec_profile = video_stream.get('profile', '')
                pix_fmt = video_stream.get('pix_fmt', '')
                
                # Check for HEVC Main 10 specifically
                if codec_name == 'hevc' and 'Main 10' in codec_profile:
                    scan_output.append(f"Video stream: {codec_name} ({codec_profile})")
                    scan_output.append(f"Pixel format: {pix_fmt}")
                    logger.info(f"HEVC Main 10 detected in {file_path}: profile={codec_profile}, pix_fmt={pix_fmt}")
                    
                    # HEVC Main 10 is a valid format, not a warning
                    # Only actual corruption or decode errors should be flagged
                    if '10' in pix_fmt:  # e.g., yuv420p10le
                        logger.debug(f"HEVC Main 10 10-bit video detected in {file_path} - valid format")
                else:
                    # Combine codec and profile info on one line for cleaner output
                    if codec_profile:
                        scan_output.append(f"Video stream: {codec_name} (Profile: {codec_profile})")
                    else:
                        scan_output.append(f"Video stream: {codec_name}")
                    
                logger.info(f"Video stream found in {file_path}: {codec_name}")
            
            # Try to get duration from multiple sources - stream level first, then container level
            duration = None
            duration_source = None
            
            # First try: stream-level duration (most accurate for the video stream)
            if video_stream and 'duration' in video_stream:
                try:
                    stream_duration = float(video_stream.get('duration', 0))
                    if stream_duration > 0:
                        duration = stream_duration
                        duration_source = 'stream'
                        logger.debug(f"Got duration from video stream for {file_path}: {duration}")
                except (ValueError, TypeError):
                    pass
            
            # Second try: container/format level duration (common for MP4, MKV, etc.)
            if duration is None and 'format' in probe and 'duration' in probe['format']:
                try:
                    format_duration = float(probe['format'].get('duration', 0))
                    if format_duration > 0:
                        duration = format_duration
                        duration_source = 'container'
                        logger.debug(f"Got duration from container format for {file_path}: {duration}")
                except (ValueError, TypeError):
                    pass
            
            # Log the duration result
            if duration is not None and duration > 0:
                scan_output.append(f"Duration: {duration:.2f}s (from {duration_source})")
                logger.info(f"Video duration for {file_path}: {duration:.2f}s (source: {duration_source})")
            else:
                # Duration missing is common for certain formats (e.g., transport streams)
                # This is a metadata limitation, not corruption
                logger.info(f"Duration metadata not available for {file_path} - this is common for certain formats")
                scan_output.append(f"Duration: not available in metadata")
        
        except ffmpeg.Error as e:
            corruption_details.append(f"FFmpeg probe error: {str(e)}")
            is_corrupted = True
            scan_output.append(f"FFmpeg probe: FAILED - {str(e)}")
            logger.warning(f"FFmpeg probe error for {file_path}: {str(e)}")
        except Exception as e:
            corruption_details.append(f"Video analysis error: {str(e)}")
            is_corrupted = True
            scan_output.append(f"Video analysis: FAILED - {str(e)}")
            logger.warning(f"Video analysis error for {file_path}: {str(e)}")
        
        # Get file size for logging purposes
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        file_size_gb = file_size / (1024 * 1024 * 1024)

        # Calculate timeout based on BOTH file size AND video duration for better accuracy
        # NAS I/O and 4K HEVC processing can be slower than expected, so be VERY generous
        # Base: 5 min + 180s per GB + video duration * 0.5 (process at 2x realtime) + 20% buffer
        # This accounts for: NAS latency, parallel I/O contention, complex codec processing, subtitle streams
        size_timeout = int(file_size_gb * 180)  # 3 minutes per GB (up from 150s)
        duration_timeout = int(duration * 0.5) if duration and duration > 0 else 0  # ~2x realtime (up from 0.4)
        base_timeout = 300 + max(size_timeout, duration_timeout)
        timeout_seconds = min(int(base_timeout * 1.2), 7200)  # Add 20% buffer, cap at 2 hours
        logger.info(f"Starting FFmpeg validation for {file_size_gb:.2f}GB file (timeout: {timeout_seconds}s, duration: {duration:.1f}s)" if duration else f"Starting FFmpeg validation for {file_size_gb:.2f}GB file (timeout: {timeout_seconds}s)")
        
        # Enhanced FFmpeg validation with best practices for thorough file checking
        try:
            # Comprehensive validation with multiple integrity checks
            # We check the ENTIRE file, not just samples, for complete validation
            result = safe_subprocess_run([
                'ffmpeg', 
                '-v', 'error',           # Show only errors
                '-err_detect', 'aggressive',  # Aggressive error detection for thorough checking
                '-fflags', '+genpts+discardcorrupt',  # Generate timestamps and handle corrupt frames
                '-analyzeduration', '100M',  # Analyze more data for better detection (100MB)
                '-probesize', '50M',     # Larger probe size for complex container formats
                '-i', file_path,         # Input file
                '-map', '0',             # Process ALL streams in the file
                '-c', 'copy',            # Copy streams to validate container integrity (fast)
                '-f', 'null',            # Null output format
                '-'                      # Output to stdout (discarded)
            ], 
            capture_output=True,
            text=True,
            timeout=timeout_seconds
            )
            
            if result.returncode != 0:
                # Check if the error should be ignored
                if not self._check_ignored_patterns(result.stderr):
                    corruption_details.append("FFmpeg validation failed")
                    is_corrupted = True
                else:
                    logger.info(f"FFmpeg error ignored due to matching pattern for {file_path}")
            
            if result.stderr:
                error_lines = result.stderr.strip().split('\n')
                # Filter for actual corruption indicators, not metadata issues or NAL unit warnings
                # NAL unit errors alone are often false positives (container/muxing issues)
                significant_errors = []
                # Ordered labels of benign patterns seen; each pattern is one
                # edit site here instead of a boolean + condition + label trio
                benign_labels = []
                opus_notice_count = 0

                def note_benign(label):
                    if label not in benign_labels:
                        benign_labels.append(label)

                for line in error_lines:
                    line_lower = line.lower()
                    if 'invalid nal unit' in line_lower:
                        # Held back from significant_errors; promoted below
                        # only when other errors exist or FFmpeg failed
                        note_benign("NAL unit errors")
                    elif 'number of reference frames' in line_lower and 'exceeds max' in line_lower:
                        # Common encoding issue that doesn't affect playback
                        note_benign("reference frame warnings")
                    elif any(pattern in line_lower for pattern in ['non monotonically increasing dts', 'application provided invalid', 'dts to muxer', 'pts to muxer']):
                        # DTS/PTS warnings are null muxer artifacts, not actual corruption
                        note_benign("DTS/PTS timestamp warnings")
                    elif 'error parsing opus packet header' in line_lower:
                        # ffmpeg 8 emits this once per Opus stream for the final
                        # packet at EOF on files ffmpeg 6 validates silently; a
                        # full audio decode of the flagged files is clean, so
                        # with exit code 0 it is a tooling artifact. Verified
                        # clean files show 1-3 lines (one per stream); a flood
                        # means mid-stream damage that -c copy cannot surface
                        # through the exit code, so it is promoted below.
                        opus_notice_count += 1
                        note_benign("Opus packet header parse notices (ffmpeg 8 EOF artifact)")
                    elif 'invalid data found when processing input' in line_lower or 'error while decoding stream' in line_lower:
                        # These are common FFmpeg decoder issues that don't always mean corruption
                        # Only mark as error if FFmpeg actually fails (returncode != 0)
                        if result.returncode != 0:
                            significant_errors.append(line)
                        else:
                            note_benign("decoder warnings (FFmpeg succeeded)")
                    elif (('error' in line_lower and 'duration' not in line_lower) or
                          'corrupt' in line_lower or
                          'broken' in line_lower or
                          'no frame' in line_lower):
                        significant_errors.append(line)
                
                if opus_notice_count > OPUS_EOF_NOTICE_MAX:
                    significant_errors.append(
                        f"{opus_notice_count} Opus packet header parse errors (mid-stream damage)")
                # Only include NAL errors if there are other errors OR if FFmpeg failed
                if 'NAL unit errors' in benign_labels and (significant_errors or result.returncode != 0):
                    # Add representative NAL error
                    significant_errors.append("Invalid NAL unit errors detected")
                
                if significant_errors:
                    corruption_details.append(f"FFmpeg errors: {'; '.join(significant_errors[:3])}")
                    is_corrupted = True
                elif benign_labels and result.returncode == 0:
                    # Benign-pattern noise only - log once with count, file stays HEALTHY
                    logger.info(f"BENIGN (healthy) in {file_path}: {len(error_lines)} lines of "
                                f"{', '.join(benign_labels)} (common in H.264/HEVC files)")
                else:
                    logger.info(f"FFmpeg completed with non-critical warnings for {file_path}")
        
        except FileNotFoundError:
            logger.warning("FFmpeg not found, skipping advanced video checks")
        except OSError as e:
            # OSError includes SIGBUS and other memory-related errors
            logger.error(f"FFmpeg process crashed with OS error for {file_path}: {str(e)}")
            corruption_details.append(f"FFmpeg process crashed (possible memory error): {str(e)}")
            is_corrupted = True
        except Exception as e:
            logger.error(f"FFmpeg validation error for {file_path}: {str(e)}")
            corruption_details.append(f"FFmpeg validation error: {str(e)}")
            is_corrupted = True
        
        try:
            result = safe_subprocess_run(
                ['ffmpeg', '-v', 'error', '-t', '10', '-i', file_path, '-f', 'null', '-'],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0 and result.stderr:
                corruption_details.append("Quick scan detected issues in first 10 seconds")
                is_corrupted = True
        
        except subprocess.TimeoutExpired:
            corruption_details.append("Quick scan timeout")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug(f"Quick scan error: {str(e)}")
        
        # Always run enhanced checks for comprehensive validation (merge deep scan into regular)
        # Since we're checking entire files anyway, might as well be thorough
        logger.info(f"Running comprehensive validation for {file_path}")
        enhanced_corrupted, enhanced_details, enhanced_output, enhanced_warnings, enhanced_notes = \
            self._enhanced_corruption_check(file_path, file_size_gb, duration)
        if enhanced_corrupted:
            is_corrupted = True
            corruption_details.extend(enhanced_details)
        if enhanced_warnings:
            warning_details.extend(enhanced_warnings)
        # The stage transcript is the only user-visible trace of warnings and
        # of operational outcomes (aborted Stage 2) that no longer set status
        if enhanced_corrupted or enhanced_warnings or enhanced_notes:
            scan_output.extend(enhanced_output)
        
        # Additional HEVC Main 10 specific checks
        if not is_corrupted and codec_name == 'hevc' and codec_profile and 'Main 10' in codec_profile:
            hevc_corrupted, hevc_details, hevc_output = self._check_hevc_main10_issues(file_path)
            if hevc_corrupted:
                is_corrupted = True
                corruption_details.extend(hevc_details)
                scan_output.extend(hevc_output)
        
        # Freeze detection. A corroborated freeze (absent packets, failing
        # decoder) is corruption; an uncorroborated long freeze is a warning.
        freeze_enabled = _setting('detection.freeze_detection_enabled')
        if freeze_enabled and duration and duration > 0:
            avg_fps = _parse_rate(video_stream.get('avg_frame_rate')) if video_stream else None
            frz_corrupted, frz_corruption, frz_warned, frz_warnings, frz_output = \
                self._check_video_freeze(file_path, duration, avg_fps=avg_fps)
            if frz_corrupted:
                is_corrupted = True
                corruption_details.extend(frz_corruption)
            if frz_warned:
                warning_details.extend(frz_warnings)
            if frz_corrupted or frz_warned:
                scan_output.extend(frz_output)

        # Return warning details as well
        return is_corrupted, corruption_details, scan_tool, scan_output, warning_details

    def _check_audio_corruption(self, file_path):
        """Check audio files for corruption using FFmpeg and format-specific tools"""
        corruption_details = []
        is_corrupted = False
        scan_tool = "ffmpeg"
        scan_output = []
        warning_details = []
        
        # Step 1: Basic FFprobe analysis
        logger.info(f"Running FFprobe on audio file: {file_path}")
        try:
            probe = _ffprobe_with_timeout(file_path)
            scan_output.append("FFprobe: PASSED")
            
            # Check for audio streams
            if 'streams' not in probe or len(probe['streams']) == 0:
                corruption_details.append("No audio streams found")
                is_corrupted = True
                scan_output.append("FFmpeg probe: No streams found")
                logger.warning(f"No streams found in {file_path}")
                return is_corrupted, corruption_details, scan_tool, scan_output, warning_details
            
            audio_stream = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)
            if not audio_stream:
                corruption_details.append("No audio stream found")
                is_corrupted = True
                scan_output.append("FFmpeg probe: No audio stream")
                logger.warning(f"No audio stream found in {file_path}")
                return is_corrupted, corruption_details, scan_tool, scan_output, warning_details
                
            # Check audio stream properties
            codec_name = audio_stream.get('codec_name', 'unknown')
            sample_rate = audio_stream.get('sample_rate', 'unknown')
            channels = audio_stream.get('channels', 'unknown')
            bit_rate = audio_stream.get('bit_rate', 'unknown')
            duration = audio_stream.get('duration', 'unknown')
            
            logger.info(f"Audio details - Codec: {codec_name}, Sample rate: {sample_rate}, Channels: {channels}, Bitrate: {bit_rate}")
            scan_output.append(f"Audio stream: {codec_name}, {sample_rate}Hz, {channels}ch")
            
        except ffmpeg.Error as e:
            stderr = e.stderr.decode('utf-8') if e.stderr else ''
            if 'Invalid data found when processing input' in stderr:
                if not self._check_ignored_patterns(stderr):
                    corruption_details.append("Invalid data found in audio file")
                    is_corrupted = True
                    scan_tool = "ffmpeg"
            elif 'moov atom not found' in stderr:
                if not self._check_ignored_patterns(stderr):
                    corruption_details.append("Missing moov atom (audio metadata)")
                    is_corrupted = True
                    scan_tool = "ffmpeg"
            else:
                if not self._check_ignored_patterns(stderr):
                    corruption_details.append(f"FFprobe error: {stderr[:100]}")
                    is_corrupted = True
                    scan_tool = "ffmpeg"
            scan_output.append(f"FFprobe: FAILED - {stderr[:200]}")
            logger.error(f"FFprobe error on audio {file_path}: {stderr[:200]}")
            return is_corrupted, corruption_details, scan_tool, scan_output, warning_details
        except subprocess.TimeoutExpired:
            probe_timeout = _setting('timeouts.ffprobe_timeout_secs')
            corruption_details.append(f"FFprobe timed out after {probe_timeout}s")
            is_corrupted = True
            scan_tool = "ffmpeg"
            scan_output.append("FFprobe: FAILED - timed out")
            logger.warning(f"FFprobe timed out for audio {file_path}")
            return is_corrupted, corruption_details, scan_tool, scan_output, warning_details

        # Step 2: Attempt to decode audio to check for corruption
        logger.info(f"Performing comprehensive audio validation for: {file_path}")
        try:
            # Enhanced audio validation - check entire file with aggressive detection
            # No more sampling - we validate the complete audio file
            result = safe_subprocess_run([
                'ffmpeg', '-v', 'error',
                '-err_detect', 'aggressive',  # Aggressive error detection for audio
                '-fflags', '+genpts+discardcorrupt',  # Handle timing and corrupt samples
                '-analyzeduration', '50M',  # Analyze more data for better detection
                '-probesize', '25M',  # Probe size for audio containers
                '-i', file_path,
                '-af', 'astats=metadata=1:reset=1,silencedetect=n=-60dB:d=1',  # Audio stats and silence detection
                '-f', 'null', '-'
            ], capture_output=True, text=True, timeout=120)
            
            if result.returncode != 0:
                stderr = result.stderr
                scan_output.append(f"Audio decode: FAILED - {stderr[:200]}")
                
                # Analyze specific audio errors
                if 'Error while decoding stream' in stderr:
                    corruption_details.append("Audio stream decoding errors detected")
                    is_corrupted = True
                elif 'Invalid frame size' in stderr:
                    corruption_details.append("Invalid audio frame size")
                    is_corrupted = True
                elif 'Header missing' in stderr:
                    corruption_details.append("Audio header missing or corrupted")
                    is_corrupted = True
                elif 'Truncated' in stderr:
                    corruption_details.append("Truncated audio file")
                    is_corrupted = True
                else:
                    # Check for specific codec errors
                    if 'mp3' in codec_name.lower() and 'Header missing' in stderr:
                        corruption_details.append("MP3 header corruption")
                        is_corrupted = True
                    elif 'flac' in codec_name.lower() and 'crc mismatch' in stderr:
                        corruption_details.append("FLAC CRC mismatch - data corruption")
                        is_corrupted = True
                    else:
                        corruption_details.append("Audio decoding failed")
                        is_corrupted = True
                        
                logger.warning(f"Audio decode failed for {file_path}: {stderr[:100]}")
            else:
                scan_output.append("Audio decode (full file): PASSED")
                logger.info(f"Audio decode test passed for {file_path}")
                
        except subprocess.TimeoutExpired:
            warning_details.append("Audio decode test timeout (file may be very large)")
            scan_output.append("Audio decode: TIMEOUT")
            logger.warning(f"Audio decode timeout for {file_path}")
        except Exception as e:
            scan_output.append(f"Audio decode: ERROR - {str(e)}")
            logger.error(f"Error during audio decode test for {file_path}: {str(e)}")
        
        # Step 3: Additional validation - check for specific audio issues
        # Now integrated into main validation above, this section handles additional checks
        if not is_corrupted:
            logger.info(f"Checking for additional audio issues in: {file_path}")
            try:
                # Check for packet corruption and timestamp issues
                result = safe_subprocess_run([
                    'ffmpeg', '-v', 'error',
                    '-err_detect', 'explode',  # Most strict - fail on any error
                    '-i', file_path,
                    '-c', 'copy',  # Copy to check container integrity
                    '-f', 'null', '-'
                ], capture_output=True, text=True, timeout=120)
                
                if result.stderr:
                    # Look for non-fatal warnings that might indicate issues
                    stderr_lower = result.stderr.lower()
                    if 'non-monotonous dts' in stderr_lower:
                        warning_details.append("Non-monotonous timestamps detected")
                    if 'queue input is backward in time' in stderr_lower:
                        warning_details.append("Timestamp inconsistencies detected")
                    if 'invalid packet size' in stderr_lower:
                        warning_details.append("Invalid packet sizes detected")
                        
                    scan_output.append(f"Deep scan warnings: {result.stderr[:200]}")
                else:
                    scan_output.append("Deep audio scan: PASSED")
                    
            except subprocess.TimeoutExpired:
                warning_details.append("Deep scan timeout")
                scan_output.append("Deep scan: TIMEOUT")
            except Exception as e:
                scan_output.append(f"Deep scan: ERROR - {str(e)}")
                logger.error(f"Error during deep audio scan for {file_path}: {str(e)}")
        
        # Step 4: Format-specific validation for lossless formats
        extension = Path(file_path).suffix.lower()
        if extension == '.flac':
            # FLAC has built-in error detection
            logger.info(f"Running FLAC-specific validation for: {file_path}")
            try:
                result = safe_subprocess_run([
                    'flac', '-t', file_path
                ], capture_output=True, text=True, timeout=60)
                
                if result.returncode != 0:
                    corruption_details.append("FLAC validation failed")
                    is_corrupted = True
                    scan_output.append(f"FLAC test: FAILED - {result.stderr[:200]}")
                else:
                    scan_output.append("FLAC test: PASSED")
            except FileNotFoundError:
                # flac command not available, skip this test
                logger.debug("FLAC command not found, skipping FLAC-specific test")
            except Exception as e:
                logger.debug(f"FLAC test error: {str(e)}")
        
        return is_corrupted, corruption_details, scan_tool, scan_output, warning_details
    
    def _check_hevc_main10_issues(self, file_path):
        """Check for HEVC Main 10 specific issues that cause green tint/freezing"""
        corruption_details = []
        is_corrupted = False
        hevc_output = []
        
        logger.info(f"Running HEVC Main 10 specific checks for {file_path}")
        hevc_output.append("=== HEVC Main 10 Analysis ===")
        
        try:
            # Check for B-frame decoding issues common in HEVC Main 10
            # Using more aggressive error detection to catch issues that cause playback freezing
            result = safe_subprocess_run([
                'ffmpeg',
                '-v', 'warning',
                '-err_detect', 'aggressive',
                '-i', ensure_cli_safe_path(file_path),
                '-vf', 'showinfo',
                '-frames:v', '100',
                '-f', 'null',
                '-'
            ], capture_output=True, text=True, timeout=30)
            
            if result.stderr:
                stderr_lower = result.stderr.lower()
                # Look for specific HEVC Main 10 decoding issues
                if 'reference picture missing' in stderr_lower:
                    corruption_details.append("HEVC reference picture errors - causes video freezing")
                    is_corrupted = True
                    hevc_output.append("Reference picture errors found (causes playback freezing)")
                
                if 'error while decoding' in stderr_lower:
                    corruption_details.append("HEVC decoding errors - video freezes while audio continues")
                    is_corrupted = True
                    hevc_output.append("Decoding errors found (VLC stops, Plex freezes video)")
                
                # Check for slice decoding errors that cause green artifacts
                if 'slice' in stderr_lower and ('error' in stderr_lower or 'invalid' in stderr_lower):
                    corruption_details.append("HEVC slice decoding errors - causes green tint/artifacts")
                    is_corrupted = True
                    hevc_output.append("Slice decoding errors (causes green tint)")
                
                # Check for SEI (Supplemental Enhancement Information) errors
                if 'sei' in stderr_lower and 'error' in stderr_lower:
                    corruption_details.append("HEVC SEI errors detected")
                    is_corrupted = True
                    hevc_output.append("SEI metadata errors found")
            
            # Check for color space conversion issues (10-bit to 8-bit)
            result = safe_subprocess_run([
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=color_space,color_transfer,color_primaries',
                '-of', 'json',
                ensure_cli_safe_path(file_path)
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0 and result.stdout:
                import json
                try:
                    probe_data = json.loads(result.stdout)
                    if probe_data.get('streams'):
                        stream = probe_data['streams'][0]
                        if stream.get('color_space') == 'bt2020nc' or stream.get('color_primaries') == 'bt2020':
                            hevc_output.append("HDR content detected (BT.2020) - requires HDR display support")
                except json.JSONDecodeError:
                    pass
                    
        except subprocess.TimeoutExpired:
            hevc_output.append("HEVC analysis timeout")
        except Exception as e:
            hevc_output.append(f"HEVC analysis error: {str(e)}")
            logger.error(f"HEVC Main 10 check error for {file_path}: {str(e)}")
        
        return is_corrupted, corruption_details, hevc_output

    def _corroborate_freeze(self, file_path, event, avg_fps=None):
        """What else is true where the picture stopped.

        Two independent signals, checked cheapest-first:
        - a packet probe over the window (no decode): if the stream barely has
          packets where the clock kept running, content is missing;
        - a decode of just the window: a flood of decoder errors means the
          picture stopped because the decoder could not produce frames.

        Returns ('missing-content'|'decode'|None, detail_line). A probe that
        fails or times out returns None: corroboration exists to prove damage,
        and an unprovable event falls through to the uncorroborated path
        rather than being promoted to corruption on no evidence.
        """
        start = event.get('start', 0)
        length = max(event.get('duration', 0), 1.0)
        lead = max(0.0, start - 3)
        span = length + 6

        try:
            probe = safe_subprocess_run([
                'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                '-read_intervals', f'{lead}%+{span}',
                '-show_entries', 'packet=pts_time', '-of', 'csv=p=0',
                file_path,
            ], capture_output=True, text=True, timeout=FREEZE_PROBE_TIMEOUT_SECS)
            pts = sorted(float(x) for x in (probe.stdout or '').split()
                         if x.strip() and x.strip() != 'N/A')
            inside = sum(1 for t in pts if start - 0.05 <= t <= start + length + 0.05)
            outside = [t for t in pts if t < start - 0.05 or t > start + length + 0.05]
            if len(pts) > 2 and _steady_cadence(outside):
                # Absent packets only mean damage on a stream that stores
                # frames at a steady rhythm. A variable-frame-rate source
                # (screen recordings, slideshows) legitimately stores nothing
                # while the picture holds, so an irregular cadence around the
                # window means this gap proves nothing. Expected packets come
                # from the file's declared average rate when it has one.
                rate = avg_fps or len(pts) / max(0.001, pts[-1] - pts[0])
                expected = length * rate
                if expected > 5 and inside < expected * FREEZE_PACKET_ABSENT_RATIO:
                    absent_pct = 100.0 * (1 - inside / expected)
                    return 'missing-content', (
                        f"Missing content at {start:.1f}s: {inside} of ~{expected:.0f} "
                        f"expected packets present ({absent_pct:.0f}% absent over {length:.1f}s)")
        except (subprocess.TimeoutExpired, OSError, ValueError) as e:
            logger.debug(f"Freeze packet probe failed for {file_path}: {e}")

        try:
            decode = safe_subprocess_run([
                'ffmpeg', '-nostdin', '-v', 'error',
                '-ss', f'{start:.3f}', '-i', file_path, '-t', f'{length:.3f}',
                '-an', '-sn', '-dn', '-map', '0:v:0', '-f', 'null', '-',
            ], capture_output=True, text=True, timeout=FREEZE_PROBE_TIMEOUT_SECS)
            errors = len(_RE_DECODE_ERROR.findall(decode.stderr or ''))
            if errors > FREEZE_DECODE_ERROR_MIN:
                return 'decode', (
                    f"Decode failure at {start:.1f}s: {errors} decoder error(s) "
                    f"over {length:.1f}s - the picture held because no frames could be produced")
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.debug(f"Freeze decode probe failed for {file_path}: {e}")

        return None, None

    def _check_video_freeze(self, file_path, duration, avg_fps=None):
        """Detect frozen video and decide whether the freeze is damage.

        freezedetect finds stretches where the picture stops changing. That
        alone does not separate damage from intent: a held animation cel and a
        static title card also stop the picture. So every candidate is
        corroborated against the file itself - absent packets or a failing
        decoder make it a corruption verdict; a freeze with neither signal is
        the picture stopping on purpose and is discounted, unless a single
        event runs past the uncorroborated minimum, which stays a warning so a
        genuinely stuck source (valid frames, nothing else wrong) is not
        silent. Black-frame overlaps are filtered first, as before.

        Returns (is_corrupted, corruption_details, has_warnings,
        warning_details, scan_output).
        """
        corruption_details = []
        warning_details = []
        is_corrupted = False
        has_warnings = False
        scan_output = []

        min_duration = _setting('detection.freeze_min_duration_secs')
        logger.info(f"Running freeze detection for {file_path}")
        scan_output.append("=== Freeze Detection Analysis ===")

        # Timeout: 2x realtime for full decode, floor 60s, cap 7200s
        timeout_seconds = max(60, min(int(duration * 2), 7200))

        try:
            result = safe_subprocess_run([
                'ffmpeg',
                '-nostdin',
                '-v', 'info',
                '-i', file_path,
                '-an',
                '-vf', (f"freezedetect=n=-60dB:d={min_duration},"
                        f"blackdetect=d=1.0:pic_th=0.98:pix_th=0.10"),
                '-f', 'null',
                '-'
            ], capture_output=True, text=True, timeout=timeout_seconds)

            stderr_lines = (result.stderr or '').split('\n')

            freeze_events = _parse_freeze_events(stderr_lines)
            black_events = []
            current_black = {}

            for line in stderr_lines:
                line_lower = line.lower()

                # Parse blackdetect events
                if 'blackdetect' in line_lower or 'black_start' in line_lower:
                    bs = _RE_BLACK_START.search(line)
                    if bs:
                        current_black['start'] = float(bs.group(1))

                    be = _RE_BLACK_END.search(line)
                    if be:
                        current_black['end'] = float(be.group(1))

                    bd = _RE_BLACK_DURATION.search(line)
                    if bd:
                        current_black['duration'] = float(bd.group(1))
                        if 'start' in current_black and 'end' in current_black:
                            black_events.append(current_black)
                        current_black = {}

            # Filter out freeze events that overlap with black sections
            # Real freezes stick on actual content, not black frames
            if freeze_events and black_events:
                total_before = len(freeze_events)
                filtered = []
                for freeze in freeze_events:
                    f_start = freeze.get('start', 0)
                    f_end = f_start + freeze.get('duration', 0)
                    overlaps_black = False
                    for black in black_events:
                        b_start = black.get('start', 0)
                        b_end = black.get('end', 0)
                        if f_start < b_end and b_start < f_end:
                            overlaps_black = True
                            break
                    if not overlaps_black:
                        filtered.append(freeze)
                removed = total_before - len(filtered)
                if removed > 0:
                    logger.info(
                        f"Filtered {removed} of {total_before} freeze events "
                        f"(black frame overlap) for {file_path}"
                    )
                    scan_output.append(
                        f"Filtered {removed} of {total_before} freeze events (black frame false positives)"
                    )
                freeze_events = filtered

            # Corroborate: a real freeze has a cause the file can show us.
            # Either the packets are absent where the clock kept running, or
            # the decoder is failing to produce pictures. Both are corruption
            # verdicts, not warnings. An event with neither signal is a held
            # animation cel or a static title/end card doing its job.
            corroborated = []
            uncorroborated = []
            for index, event in enumerate(freeze_events):
                if index >= FREEZE_CORROBORATE_MAX_EVENTS:
                    uncorroborated.append(event)
                    continue
                cause, detail = self._corroborate_freeze(file_path, event, avg_fps)
                if cause:
                    corroborated.append((cause, detail, event))
                else:
                    uncorroborated.append(event)
            if len(freeze_events) > FREEZE_CORROBORATE_MAX_EVENTS:
                scan_output.append(
                    f"Corroborated the first {FREEZE_CORROBORATE_MAX_EVENTS} of "
                    f"{len(freeze_events)} freeze event(s)")

            if corroborated:
                is_corrupted = True
                causes = sorted({c for c, _, _ in corroborated})
                n = len(corroborated)
                if causes == ['missing-content']:
                    summary = f"Missing content: {n} frozen segment(s) with absent packets"
                elif causes == ['decode']:
                    summary = f"Decode failure: {n} frozen segment(s) with decoder errors"
                else:
                    summary = (f"Damaged video: {n} frozen segment(s) with "
                               f"missing packets or decoder errors")
                corruption_details.append(summary)
                scan_output.append(summary)
                for _, detail, _ in corroborated[:10]:
                    scan_output.append(f"  {detail}")
                logger.warning(
                    f"Corroborated freeze damage in {file_path}: {summary}")

            long_minimum = _setting('detection.freeze_uncorroborated_min_secs')
            reportable = [e for e in uncorroborated
                          if e.get('duration', 0) >= long_minimum]
            discounted = [e for e in uncorroborated
                          if e.get('duration', 0) < long_minimum]
            for event in discounted:
                scan_output.append(
                    f"Discounted still segment at {event.get('start', 0):.1f}s "
                    f"({event.get('duration', 0):.1f}s) - packets present and decodable, "
                    f"the picture stopped on purpose")

            if reportable:
                has_warnings = True
                total_frozen = min(_merged_frozen_seconds(reportable), duration)
                frozen_pct = min(total_frozen / duration * 100, 100.0)

                summary = (
                    f"Video freeze warning: {len(reportable)} event(s), "
                    f"{total_frozen:.1f}s frozen ({frozen_pct:.1f}% of video)"
                )
                warning_details.append(summary)
                scan_output.append(summary)

                for i, event in enumerate(reportable[:10]):
                    scan_output.append(
                        f"  Freeze #{i + 1}: {event.get('start', 0):.1f}s - "
                        f"{event.get('end', 0):.1f}s (duration: {event.get('duration', 0):.1f}s)"
                    )
                if len(reportable) > 10:
                    scan_output.append(f"  ... and {len(reportable) - 10} more freeze event(s)")

                logger.info(
                    f"Freeze warning in {file_path}: {len(reportable)} event(s), "
                    f"{total_frozen:.1f}s total frozen"
                )
            if not corroborated and not reportable:
                scan_output.append("No freeze events detected")
                logger.info(f"No freeze detected in {file_path}")

        except subprocess.TimeoutExpired:
            scan_output.append(f"Freeze detection timed out after {timeout_seconds}s")
            logger.warning(f"Freeze detection timeout for {file_path} ({timeout_seconds}s)")
        except FileNotFoundError:
            logger.warning("FFmpeg not found, skipping freeze detection")
        except OSError as e:
            # OSError includes SIGBUS and other memory-related errors
            logger.error(f"Freeze detection process crashed for {file_path}: {str(e)}")
        except Exception as e:
            logger.debug(f"Freeze detection error for {file_path}: {str(e)}")

        scan_output.append("=== Freeze Detection Complete ===")
        return is_corrupted, corruption_details, has_warnings, warning_details, scan_output

    def _enhanced_corruption_check(self, file_path, file_size_gb, duration=None):
        """Enhanced multi-stage corruption detection for files that fail basic checks

        Args:
            duration: video duration in seconds if the caller already probed it,
                so the sampling stages don't re-probe the same container.
        """
        corruption_details = []
        is_corrupted = False
        enhanced_output = []
        warning_details = []
        
        logger.info(f"Starting enhanced corruption analysis for {file_path}")
        enhanced_output.append(f"=== Enhanced Corruption Analysis for {file_size_gb:.2f}GB file ===")
        
        # Stage 1: Frame count verification
        frame_corrupted, frame_details, frame_warnings, frame_notes = self._check_frame_integrity(file_path)
        enhanced_output.append("Stage 1: Frame integrity check")
        if frame_corrupted:
            is_corrupted = True
            corruption_details.extend(frame_details)
            enhanced_output.append(f"  Result: FAILED - {'; '.join(frame_details)}")
        elif frame_warnings:
            warning_details.extend([f"Stage 1: {detail}" for detail in frame_warnings])
            enhanced_output.append(f"  Result: WARNING - {'; '.join(frame_warnings)}")
        elif frame_notes:
            # Never claim PASSED for a check that did not measure the file
            enhanced_output.append(f"  Result: INCOMPLETE - {'; '.join(frame_notes)}")
        else:
            enhanced_output.append("  Result: PASSED")
        
        # Stage 2: Temporal outlier detection (for files > 1GB)
        if file_size_gb > 1.0:
            temporal_corrupted, temporal_details, temporal_warnings, temporal_notes = \
                self._check_temporal_outliers(file_path, duration)
            has_notes = bool(temporal_notes)
            enhanced_output.append("Stage 2: Temporal outlier detection")
            if temporal_corrupted:
                is_corrupted = True
                corruption_details.extend(temporal_details)
                enhanced_output.append(f"  Result: FAILED - {'; '.join(temporal_details)}")
            elif temporal_warnings:
                warning_details.extend([f"Stage 2: {detail}" for detail in temporal_warnings])
                enhanced_output.append(f"  Result: WARNING - {'; '.join(temporal_warnings)}")
            elif temporal_notes:
                # No verdict was reached (timeouts / under-sampling) - never
                # claim PASSED for a check that did not measure the file
                enhanced_output.append(f"  Result: INCOMPLETE - {'; '.join(temporal_notes)}")
                temporal_notes = []
            else:
                enhanced_output.append("  Result: PASSED")
            for note in temporal_notes:
                enhanced_output.append(f"  Note: {note}")
        else:
            has_notes = False
            enhanced_output.append("Stage 2: Skipped (file < 1GB)")
        
        # Stage 3: Multi-point sampling for large files
        if file_size_gb > 5.0:
            sampling_corrupted, sampling_details, sampling_warnings = self._check_multipoint_sampling(file_path, duration)
            enhanced_output.append("Stage 3: Multi-point sampling")
            if sampling_corrupted:
                is_corrupted = True
                corruption_details.extend(sampling_details)
                enhanced_output.append(f"  Result: FAILED - {'; '.join(sampling_details)}")
            elif sampling_warnings:
                warning_details.extend([f"Stage 3: {detail}" for detail in sampling_warnings])
                enhanced_output.append(f"  Result: WARNING - {'; '.join(sampling_warnings)}")
            else:
                enhanced_output.append("  Result: PASSED")
        else:
            enhanced_output.append("Stage 3: Skipped (file < 5GB)")
        
        # Stage 4: Enhanced error detection with strict flags (warnings only - doesn't mark as corrupted)
        strict_corrupted, strict_details = self._check_strict_error_detection(file_path)
        enhanced_output.append("Stage 4: Strict error detection (warnings only)")
        if strict_corrupted:
            # Don't mark as corrupted - these are usually container/muxing issues, not actual corruption
            warning_details.extend([f"Stage 4: {detail}" for detail in strict_details])
            enhanced_output.append(f"  Result: WARNING - {'; '.join(strict_details)}")
        else:
            enhanced_output.append("  Result: PASSED")
        
        enhanced_output.append(f"=== Enhanced Analysis Complete: {'CORRUPTED' if is_corrupted else 'CLEAN'} ===")
        return is_corrupted, corruption_details, enhanced_output, warning_details, has_notes
    
    def _probe_stream_counts(self, file_path, count_flag, count_key, timeout):
        """One ffprobe pass; returns (framerate, count, duration) or None if unavailable.

        Uses key=value output because ffprobe orders csv columns by its own
        section layout, not the requested order (the old csv parser expected a
        column count ffmpeg 8 never emits, so the check silently never ran).
        The video stream's own duration is preferred (a container can carry
        audio longer than the video track); the format duration is only the
        fallback for Matroska streams that report duration=N/A. avg_frame_rate
        is used instead of r_frame_rate so variable-frame-rate content does
        not overshoot the expected count.
        """
        result = safe_subprocess_run([
            'ffprobe',
            '-show_entries', f'stream=avg_frame_rate,duration,{count_key}:format=duration',
            '-select_streams', 'v:0',
            count_flag,
            '-of', 'default=noprint_wrappers=1',
            '-v', 'quiet',
            ensure_cli_safe_path(file_path)
        ], capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        values = {}
        for line in result.stdout.strip().splitlines():
            key, sep, value = line.partition('=')
            if sep and value and value != 'N/A' and key not in values:
                values[key] = value
        try:
            framerate_str = values['avg_frame_rate']
            if '/' in framerate_str:
                num, den = map(float, framerate_str.split('/'))
                framerate = num / den if den != 0 else 0.0
            else:
                framerate = float(framerate_str)
            count = int(values[count_key])
            duration = float(values['duration'])
        except (KeyError, ValueError):
            return None
        if framerate <= 0 or duration <= 0:
            return None
        return framerate, count, duration

    @staticmethod
    def _frame_mismatch(framerate, count, duration):
        """Return (expected, diff, diff_percent) for a counted stream"""
        expected = int(framerate * duration)
        diff = abs(expected - count)
        return expected, diff, (diff / expected * 100) if expected > 0 else 0

    def _check_frame_integrity(self, file_path):
        """Compare decodable frame count against container metadata expectations.

        Returns (is_corrupted, corruption_details, warning_details). A confirmed
        mismatch is a warning, never a corruption verdict: container framerate
        lies on sparse-video and variable-frame-rate files (a 240s QuickTime
        with 244 real frames declares 25fps), and real decode damage is caught
        by the err_detect deep-decode stage. Only an ffprobe process crash
        marks corruption here.
        """
        corruption_details = []
        warning_details = []
        info_notes = []
        is_corrupted = False

        try:
            logger.info(f"Checking frame integrity for {file_path}")
            # Cheap pass first: -count_packets does no decode. The full
            # -count_frames decode is sequential and can take minutes on large
            # files, so it only runs to confirm an ambiguous packet count.
            # (Header metadata such as nb_frames cannot stand in for this pass:
            # a truncated mdat with an intact moov still claims the full sample
            # count, so only counting what is actually present has signal.)
            packets = self._probe_stream_counts(
                file_path, '-count_packets', 'nb_read_packets', timeout=120)
            if packets:
                framerate, packet_count, duration = packets
                expected, diff, diff_percent = self._frame_mismatch(framerate, packet_count, duration)
                logger.info(f"Frame analysis (packets): expected {expected}, "
                            f"found {packet_count}, diff {diff} ({diff_percent:.1f}%)")
                if diff_percent > 5.0:
                    # Packets and frames can legitimately differ; confirm with a decode
                    decoded = self._probe_stream_counts(
                        file_path, '-count_frames', 'nb_read_frames', timeout=120)
                    if decoded:
                        framerate, frame_count, duration = decoded
                        expected, diff, diff_percent = self._frame_mismatch(framerate, frame_count, duration)
                        logger.info(f"Frame analysis (decoded): expected {expected}, "
                                    f"found {frame_count}, diff {diff} ({diff_percent:.1f}%)")
                        if diff_percent > 5.0:
                            warning_details.append(
                                f"Frame count differs from container metadata by {diff} frames "
                                f"({diff_percent:.1f}%) - possible missing frames or "
                                f"sparse/variable frame rate content")
                    else:
                        # Heavily damaged files are exactly where the confirm
                        # decode errors out; the packet evidence must not
                        # vanish with it
                        warning_details.append(
                            f"Packet count differs from container metadata by {diff} frames "
                            f"({diff_percent:.1f}%) and decode confirmation failed")

        except subprocess.TimeoutExpired:
            # Operational outcome, not a file signal (Stage 2's pattern):
            # surfaced as INCOMPLETE, never as warning status
            info_notes.append("Frame integrity check timed out; result inconclusive")
        except OSError as e:
            # OSError includes SIGBUS and other memory-related errors
            logger.error(f"FFprobe process crashed with OS error for {file_path}: {str(e)}")
            corruption_details.append(f"FFprobe crashed (possible memory error)")
            is_corrupted = True
        except Exception as e:
            logger.debug(f"Frame integrity check error: {str(e)}")

        return is_corrupted, corruption_details, warning_details, info_notes
    
    def _check_temporal_outliers(self, file_path, duration=None):
        """Detect temporal outliers that indicate visual corruption using signalstats

        Samples TEMPORAL_SAMPLE_POSITIONS windows from the body of the file. The
        `movie=` lavfi source this previously used silently stops after the first
        few seconds and exits 0, so the statistics only ever described the intro
        (fades and title cards), which is where vertical-line repetition is
        legitimately highest.

        TOUT (temporal outliers) warns; VREP (vertical line repetition) is an
        informational note only. Measured against real damage neither metric
        discriminates well, because signalstats is analog-tape QC tooling:
        film grain pushes TOUT past its per-frame threshold on 46-100% of
        frames of a pristine Bluray episode, and VREP tracks dark scenes,
        letterboxing and monochrome lighting - 298 production files flagged,
        spot checks across the 10-96% range all visually pristine, and clean
        synthetic content outscores genuinely corrupted content.

        Args:
            duration: video duration in seconds if the caller already probed it;
                probed here when omitted.

        Returns: (is_corrupted, corruption_details, warning_details, info_notes)

        info_notes carries operational outcomes (timeouts, under-sampling) that
        describe this run of the check, not the file - they belong in scan
        output, never in warning status.
        """
        corruption_details = []
        warning_details = []
        info_notes = []
        is_corrupted = False

        try:
            if duration is None:
                duration = _probe_video_duration(file_path)
            if duration is None:
                logger.info(f"Temporal outlier check skipped for {file_path}: no usable duration")
                return is_corrupted, corruption_details, warning_details, info_notes

            logger.info(f"Checking temporal outliers for {file_path} (duration: {duration:.1f}s)")

            tout_values = []
            vrep_values = []
            timed_out = False

            for position in TEMPORAL_SAMPLE_POSITIONS:
                start_time = duration * position
                try:
                    result = safe_subprocess_run([
                        'ffmpeg',
                        '-nostats',
                        '-v', 'error',
                        '-ss', f'{start_time:.2f}',
                        '-i', ensure_cli_safe_path(file_path),
                        '-map', '0:v:0',
                        '-t', str(_setting('performance.temporal_sample_secs')),
                        '-vf', 'signalstats=stat=tout+vrep,metadata=print:file=-',
                        '-f', 'null',
                        '-'
                    ], capture_output=True, text=True, timeout=_setting('timeouts.temporal_sample_timeout_secs'))
                except subprocess.TimeoutExpired:
                    # One slow window must not discard the windows that did read,
                    # but whatever made this window slow (stalled mount, failing
                    # sectors) applies to the rest, so don't pay the timeout again.
                    logger.warning(f"Temporal sample at {start_time:.0f}s timed out for {file_path}")
                    if timed_out:
                        break
                    timed_out = True
                    continue

                # stderr is deliberately ignored: seeking mid-stream emits
                # version-dependent decoder noise that says nothing about the
                # file's integrity (see _check_multipoint_sampling).
                tout_values.extend(float(v) for v in _RE_SIGNALSTATS_TOUT.findall(result.stdout))
                vrep_values.extend(float(v) for v in _RE_SIGNALSTATS_VREP.findall(result.stdout))

            total_frames = max(len(tout_values), len(vrep_values))
            min_frames = _setting('performance.temporal_min_frames')
            if total_frames < min_frames:
                detail = (f"Temporal outlier check sampled only {total_frames} frame(s), "
                          f"below the {min_frames}-frame minimum")
                logger.warning(f"{detail} for {file_path}")
                info_notes.append(detail)
                return is_corrupted, corruption_details, warning_details, info_notes

            # Guarded independently: truncated output can yield one metric without
            # the other, and signalstats emits them as separate metadata lines.
            tout_percent = (sum(1 for v in tout_values if v > TEMPORAL_TOUT_FRAME) / len(tout_values) * 100
                            if tout_values else 0.0)
            vrep_percent = (sum(1 for v in vrep_values if v > TEMPORAL_VREP_FRAME) / len(vrep_values) * 100
                            if vrep_values else 0.0)

            logger.info(f"Temporal analysis over {total_frames} sampled frames: "
                        f"{tout_percent:.1f}% outliers, {vrep_percent:.1f}% vertical repetition")

            # TOUT warns; VREP is informational only - rationale in the docstring
            if tout_percent > TEMPORAL_TOUT_PERCENT:
                warning_details.append(f"High temporal outliers detected: {tout_percent:.1f}% of frames")
            if vrep_percent > TEMPORAL_VREP_PERCENT:
                info_notes.append(
                    f"Elevated vertical line repetition: {vrep_percent:.1f}% of sampled frames")
            if timed_out:
                info_notes.append("Temporal outlier check partially timed out")

        except subprocess.TimeoutExpired:
            # Raised by the duration probe; the per-window timeout is handled above.
            info_notes.append("Temporal outlier check timed out")
        except OSError as e:
            # OSError includes SIGBUS and other memory-related errors
            logger.error(f"FFmpeg process crashed with OS error during temporal outlier check for {file_path}: {str(e)}")
            corruption_details.append("Temporal outlier check crashed (possible memory error)")
            is_corrupted = True
        except Exception as e:
            # Don't hide an incomplete deep check at debug level: a swallowed
            # error here means this corruption signal did not actually run.
            logger.warning(f"Temporal outlier check did not complete for {file_path}: {str(e)}")
            info_notes.append("Temporal outlier check did not complete")

        return is_corrupted, corruption_details, warning_details, info_notes
    
    def _check_multipoint_sampling(self, file_path, duration=None):
        """Check beginning, middle, and end of large files for corruption

        Args:
            duration: video duration in seconds if the caller already probed it;
                probed here when omitted.

        Returns: (is_corrupted, corruption_details, warning_details)
        """
        corruption_details = []
        warning_details = []
        is_corrupted = False

        try:
            if duration is None:
                duration = _probe_video_duration(file_path)

            # If we don't have duration, skip multipoint sampling
            if duration is None:
                return is_corrupted, corruption_details, warning_details

            sample_points = [
                (0, 10, "beginning"),
                (duration * 0.5, 10, "middle"), 
                (max(0, duration - 10), 10, "end")
            ]
            
            logger.info(f"Multi-point sampling for {file_path} (duration: {duration:.1f}s)")
            
            for start_time, sample_duration, location in sample_points:
                try:
                    # No aggressive error detection flags - seeking mid-stream triggers
                    # false positives (PPS errors, first slice missing, etc.)
                    # Just check if FFmpeg can decode frames at this position
                    result = safe_subprocess_run([
                        'ffmpeg',
                        '-v', 'error',
                        '-ss', str(start_time),
                        '-t', str(sample_duration),
                        '-i', file_path,
                        '-f', 'null',
                        '-'
                    ], capture_output=True, text=True, timeout=30)

                    # Stage 3 NEVER marks as corrupted - seeking causes version-dependent
                    # false positives (FFmpeg 6.x returns non-zero exit code for PPS errors
                    # during seeking, FFmpeg 8.x returns 0). Stage 1 (full decode from
                    # beginning) is the authoritative corruption check.
                    if result.returncode != 0 or result.stderr:
                        logger.debug(f"Stage 3 {location} noise (informational): {result.stderr[:200] if result.stderr else 'exit ' + str(result.returncode)}")
                    else:
                        logger.debug(f"Multi-point sample {location} OK for {file_path}")

                except subprocess.TimeoutExpired:
                    logger.debug(f"Stage 3 {location} timeout (informational)")
                    
        except Exception as e:
            # Surface that a deep check did not complete instead of silently
            # reporting the file healthy.
            logger.warning(f"Multi-point sampling did not complete for {file_path}: {str(e)}")
            warning_details.append("Multi-point sampling did not complete")

        return is_corrupted, corruption_details, warning_details
    
    def _check_strict_error_detection(self, file_path):
        """Enhanced error detection with strict flags - returns warnings only, not corruption
        
        These checks are extremely sensitive and often flag container/muxing issues
        that don't affect playback. Results should be treated as warnings."""
        corruption_details = []
        is_corrupted = False
        
        try:
            logger.info(f"Running strict error detection for {file_path}")
            result = safe_subprocess_run([
                'ffmpeg',
                '-v', 'error',
                '-err_detect', 'crccheck+bitstream+buffer+explode',
                '-i', file_path,
                '-t', '30',  # First 30 seconds with strict checking
                '-f', 'null',
                '-'
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                corruption_details.append("Strict error detection failed")
                # Don't mark as corrupted - these are warnings only
            
            if result.stderr:
                # Enhanced error pattern recognition
                # NAL unit errors are often false positives (container/muxing issues)
                # Only mark as corrupted if there are multiple types of errors
                error_patterns = [
                    ('invalid nal unit', 'Invalid NAL unit structure'),
                    ('error while decoding mb', 'Macroblock decoding error'),
                    ('cabac decode', 'CABAC decoding failure'),
                    ('concealing errors', 'Error concealment activated'),
                    ('corrupted frame', 'Frame corruption detected'),
                    ('packet corrupt', 'Packet corruption detected'),
                    ('crc mismatch', 'CRC checksum failure')
                ]
                
                stderr_lower = result.stderr.lower()
                found_errors = []
                nal_unit_only = True
                
                for pattern, description in error_patterns:
                    if pattern in stderr_lower:
                        found_errors.append(description)
                        if pattern != 'invalid nal unit':
                            nal_unit_only = False
                        logger.info(f"Detected: {description} in {file_path}")
                
                # Stage 4 no longer marks as corrupted - all findings are warnings
                if found_errors:
                    corruption_details.extend(found_errors)
                    # Don't set is_corrupted = True - these are warnings only
                    logger.info(f"Stage 4 warnings (not marking as corrupted): {', '.join(found_errors)}")
                elif nal_unit_only and result.returncode == 0:
                    logger.info(f"NAL unit errors only (not marking as corrupted) for {file_path}")
                    # Don't mark as corrupted, but include in details for warning handling
                    corruption_details.append("NAL unit warnings only (strict mode)")
                    # Note: The calling function will handle this as a warning
                        
        except subprocess.TimeoutExpired:
            corruption_details.append("Strict error detection timeout")
        except Exception as e:
            logger.debug(f"Strict error detection error: {str(e)}")
        
        return is_corrupted, corruption_details
    
    def find_orphaned_records(self, scan_results_db):
        """Find database records for files that no longer exist"""
        orphaned_records = []
        
        for result in scan_results_db:
            if not os.path.exists(result.file_path):
                orphaned_records.append(result)
        
        return orphaned_records
    
    def get_current_scan_info(self):
        """Get current scan progress information"""
        with self.scan_lock:
            if self.current_scan_file and self.scan_start_time:
                elapsed = time.time() - self.scan_start_time
                return {
                    'current_file': self.current_scan_file,
                    'elapsed_time': elapsed,
                    'is_scanning': True
                }
            return {
                'current_file': None,
                'elapsed_time': 0,
                'is_scanning': False
            }
    
    def _check_cache(self, file_path, file_hash, last_modified):
        """Check if we have a valid cached scan result for this file"""
        if not self.database_path:
            return None
            
        session = None
        try:
            from pixelprobe.models import ScanResult
            session = self._get_db_session()
            if not session:
                return None
            
            # Check for existing scan result
            result = session.query(ScanResult).filter_by(file_path=file_path).first()
            
            if result and result.scan_date:
                # Skip cache for pending files - they must be scanned
                if result.scan_status == 'pending':
                    session.close()
                    return None
                    
                # Content hash equality alone proves the cached result is
                # current. The old additional mtime-equality requirement broke
                # after the UTC mtime fix: pre-upgrade rows store naive LOCAL
                # time, so on any TZ != UTC host every cached row would miss
                # and force a full library re-decode. mtime remains as the
                # fallback for rows scanned before hashes were stored.
                if ((result.file_hash and file_hash and result.file_hash == file_hash) or
                    (not result.file_hash and
                     result.last_modified and last_modified and
                     result.last_modified.replace(tzinfo=None) == last_modified.replace(tzinfo=None))):
                    
                    # Convert database result to expected format
                    cached_data = {
                        'file_path': result.file_path,
                        'file_size': result.file_size,
                        'file_type': result.file_type,
                        'creation_date': result.creation_date,
                        'last_modified': result.last_modified,
                        'is_corrupted': result.is_corrupted,
                        'corruption_details': result.corruption_details,
                        'file_hash': result.file_hash,
                        'scan_tool': result.scan_tool,
                        'scan_duration': result.scan_duration,
                        'scan_output': result.scan_output,
                        'has_warnings': result.has_warnings,
                        'warning_details': result.warning_details
                    }
                    return cached_data
            
        except Exception as e:
            logger.error(f"Error checking cache for {file_path}: {e}")
        finally:
            if session:
                session.close()
        
        return None
    
    def _save_to_cache(self, file_path, scan_result):
        """Save scan result to database cache"""
        if not self.database_path:
            return

        session = None
        try:
            from pixelprobe.models import ScanResult
            from datetime import datetime, timezone
            import traceback

            session = self._get_db_session()
            if not session:
                logger.warning(f"No database session available for caching {file_path}")
                return

            # Only rollback if session is in a failed/invalid state
            # Don't rollback unconditionally as it disrupts concurrent operations on shared sessions
            try:
                # Check if session is in a bad state by attempting a simple operation
                # If it fails, we'll catch it and rollback
                session.expire_all()
            except Exception as e:
                # Session is in bad state, rollback and retry
                logger.debug(f"Session in bad state, rolling back: {e}")
                try:
                    session.rollback()
                    session.expire_all()
                except:
                    pass

            db_result = session.query(ScanResult).filter_by(file_path=file_path).first()

            if not db_result:
                # Record doesn't exist - this shouldn't happen as records are created during discovery
                # Log a warning but continue to create the record
                logger.warning(f"Record not found for {file_path} during save - creating new record")
                db_result = ScanResult(file_path=file_path)
                session.add(db_result)

            # Update with scan results
            db_result.file_size = scan_result.get('file_size')
            db_result.file_type = scan_result.get('file_type')
            db_result.creation_date = scan_result.get('creation_date')
            if not apply_scan_baseline(db_result, scan_result.get('file_hash'),
                                       scan_result.get('last_modified')):
                # Anti-laundering: a rescan of a bitrot-suspected file must not
                # adopt its current content as the baseline - a bit flip can
                # pass decode checks and would silently become the new "good"
                # hash. Baseline updates for flagged files happen only via
                # auto-expire or the manual accept action.
                logger.info(f"Preserving hash/mtime baseline for bitrot-suspected file: {file_path}")
            if retire_stale_override(db_result, scan_result.get('file_hash'),
                                     scan_result.get('corruption_details'),
                                     scan_result.get('warning_details')):
                logger.info(f"Override retired for {file_path}: new findings outside its scope")
            db_result.is_corrupted = scan_result.get('is_corrupted', False)
            db_result.corruption_details = scan_result.get('corruption_details')
            db_result.scan_tool = scan_result.get('scan_tool')
            db_result.scan_duration = scan_result.get('scan_duration')
            db_result.scan_output = scan_result.get('scan_output')
            db_result.has_warnings = scan_result.get('has_warnings', False)
            db_result.warning_details = scan_result.get('warning_details')
            db_result.scan_date = datetime.now(timezone.utc)
            db_result.scan_status = 'completed'
            db_result.file_exists = True

            # Flush first to catch any database errors before commit
            session.flush()
            session.commit()

            # Only log success AFTER commit succeeds
            self.successful_saves += 1
            logger.info(f"Successfully saved scan result for {file_path}")
        except Exception as e:
            import traceback
            self.failed_saves += 1
            logger.error(f"Error saving to cache for {file_path}: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            logger.error(f"Failed saves so far: {self.failed_saves}")
            if session:
                try:
                    session.rollback()
                except Exception as rollback_error:
                    logger.error(f"Error during rollback: {rollback_error}")
        finally:
            if session:
                try:
                    session.close()
                except Exception as close_error:
                    logger.error(f"Error closing session: {close_error}")

    def get_save_stats(self):
        """Get statistics about successful and failed database saves"""
        return {
            'successful_saves': self.successful_saves,
            'failed_saves': self.failed_saves
        }

    def reset_save_stats(self):
        """Reset save statistics counters"""
        self.successful_saves = 0
        self.failed_saves = 0

    def _check_ignored_patterns(self, error_output):
        """Check if error output contains any ignored patterns"""
        if not self.database_path or not error_output:
            return False
            
        session = None
        try:
            from pixelprobe.models import IgnoredErrorPattern
            session = self._get_db_session()
            if not session:
                return False
            
            # Get active ignored patterns
            patterns = session.query(IgnoredErrorPattern).filter_by(is_active=True).all()
            
            # Check if any pattern matches the error output
            for pattern in patterns:
                if pattern.pattern.lower() in error_output.lower():
                    logger.info(f"Error output matches ignored pattern: {pattern.pattern}")
                    return True
            
        except Exception as e:
            logger.error(f"Error checking ignored patterns: {e}")
        finally:
            if session:
                session.close()
        
        return False
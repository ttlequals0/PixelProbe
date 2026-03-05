"""
Helper utilities for PixelProbe
"""

import os
import logging
import time
from datetime import datetime, timezone

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
        from pixelprobe.models import ScanConfiguration
        configs = ScanConfiguration.query.filter_by(is_active=True).all()
        paths = [config.path for config in configs if config.path]
        if paths:
            return paths
    except Exception:
        pass

    scan_paths_env = os.environ.get('SCAN_PATHS', '')
    return [p.strip() for p in scan_paths_env.split(',') if p.strip()]


class ProgressTracker:
    """Unified progress tracking to eliminate redundant progress code

    This class handles progress tracking for all operations (scan, cleanup, file-changes)
    in a consistent way, eliminating duplicate progress calculation logic.
    """

    def __init__(self, operation_type):
        self.operation_type = operation_type
        self.start_time = time.time()
        self.phase_start_time = time.time()

        # Define phase weights for different operation types
        self.phase_weights = {
            'scan': [0.2, 0.1, 0.7],  # Discovery: 20%, Adding: 10%, Scanning: 70%
            'cleanup': [0.1, 0.8, 0.1],     # Scanning DB: 10%, Checking files: 80%, Deleting: 10%
            'file_changes': [0.05, 0.8, 0.15]  # Starting: 5%, Checking: 80%, Verifying: 15%
        }

    def calculate_progress_percentage(self, phase_number, phase_current, phase_total, total_phases=3):
        """Calculate overall progress percentage based on phase weights"""
        if phase_total == 0:
            return 0

        weights = self.phase_weights.get(self.operation_type, [1.0 / total_phases] * total_phases)

        # Calculate progress within current phase
        phase_progress = (phase_current / phase_total) if phase_total > 0 else 0

        # Calculate total progress
        completed_weight = sum(weights[:phase_number - 1]) if phase_number > 1 else 0
        current_weight = weights[phase_number - 1] if phase_number <= len(weights) else 0

        total_progress = completed_weight + (current_weight * phase_progress)
        return min(total_progress * 100, 100)

    def estimate_time_remaining(self, files_processed, total_files):
        """Estimate time remaining based on current progress"""
        if files_processed == 0 or total_files == 0:
            return None

        elapsed = time.time() - self.start_time

        # Minimum elapsed time to avoid division issues
        if elapsed < 1:
            return "calculating..."

        rate = files_processed / elapsed
        remaining_files = total_files - files_processed

        # If no files remaining, we're done
        if remaining_files <= 0:
            return "completing..."

        # If rate is extremely low (very slow scan), return a special message
        if rate < 0.001:  # Less than 1 file per 1000 seconds
            return "processing..."
        elif rate > 0:
            remaining_seconds = remaining_files / rate
            # Cap at 30 days to avoid unrealistic ETAs
            if remaining_seconds > 2592000:  # 30 days
                return ">30 days"
            return self.format_time(remaining_seconds)
        return None

    @staticmethod
    def format_time(seconds):
        """Format seconds into human-readable time"""
        # Handle invalid or very small values
        if seconds <= 0:
            return "calculating..."
        elif seconds < 60:
            # Don't show 0s, minimum 1s
            return f"{max(1, int(seconds))}s"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m {int(seconds % 60)}s"
        elif seconds < 86400:  # Less than a day
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours}h {minutes}m"
        else:
            # For very long ETAs, show days
            days = int(seconds / 86400)
            hours = int((seconds % 86400) / 3600)
            return f"{days}d {hours}h"

    def get_progress_message(self, phase_name, files_processed=0, total_files=0, current_file=None):
        """Generate consistent progress messages"""
        eta = self.estimate_time_remaining(files_processed, total_files)
        eta_str = f" ETA: {eta}" if eta else ""

        if current_file:
            return f"{phase_name}: current file: {current_file} - {files_processed} of {total_files:,} files{eta_str}"
        else:
            return f"{phase_name}: {files_processed} of {total_files:,} files{eta_str}"

def log_operation_status(operation_type, status, details=None):
    """Centralized operation status logging"""
    log_messages = {
        'start': f"=== {operation_type.upper()} STARTED ===",
        'complete': f"=== {operation_type.upper()} COMPLETED ===",
        'error': f"=== {operation_type.upper()} FAILED ===",
        'cancel': f"=== {operation_type.upper()} CANCELLED ==="
    }

    message = log_messages.get(status, f"{operation_type} status: {status}")
    logger.info(message)

    if details:
        for key, value in details.items():
            logger.info(f"{key}: {value}")


def batch_process(items, batch_size=1000):
    """Generator to process items in batches"""
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]


def create_state_dict(state_obj, extra_fields=None):
    """Create a standardized dictionary from state objects"""
    base_dict = {
        'id': state_obj.id,
        'is_active': state_obj.is_active,
        'phase': state_obj.phase,
        'phase_number': state_obj.phase_number,
        'phase_current': state_obj.phase_current,
        'phase_total': state_obj.phase_total,
        'files_processed': state_obj.files_processed,
        'total_files': getattr(state_obj, 'total_files', state_obj.estimated_total if hasattr(state_obj, 'estimated_total') else 0),
        'start_time': state_obj.start_time.isoformat() if state_obj.start_time else None,
        'end_time': state_obj.end_time.isoformat() if state_obj.end_time else None,
        'current_file': state_obj.current_file,
        'progress_message': state_obj.progress_message,
        'error_message': state_obj.error_message
    }

    # Add operation-specific ID field
    if hasattr(state_obj, 'scan_id'):
        base_dict['scan_id'] = state_obj.scan_id
    elif hasattr(state_obj, 'cleanup_id'):
        base_dict['cleanup_id'] = state_obj.cleanup_id
    elif hasattr(state_obj, 'check_id'):
        base_dict['check_id'] = state_obj.check_id

    # Add any extra fields
    if extra_fields:
        for field in extra_fields:
            if hasattr(state_obj, field):
                value = getattr(state_obj, field)
                # Handle datetime serialization
                if isinstance(value, datetime):
                    value = value.isoformat()
                base_dict[field] = value

    return base_dict


def mark_operation_complete(state_obj, message=None):
    """Common method to mark operations as complete"""
    state_obj.is_active = False
    state_obj.phase = 'completed'
    state_obj.end_time = datetime.now(timezone.utc)
    if message:
        state_obj.progress_message = message
    return state_obj


def mark_operation_error(state_obj, error_message):
    """Common method to mark operations as failed"""
    state_obj.is_active = False
    state_obj.phase = 'error'
    state_obj.end_time = datetime.now(timezone.utc)
    state_obj.error_message = error_message
    return state_obj


class OperationCancelledException(Exception):
    """Custom exception for when an operation is cancelled"""
    pass

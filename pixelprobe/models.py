from datetime import datetime, timezone, timedelta
from flask_sqlalchemy import SQLAlchemy
import json
import uuid
import logging
import os
import bcrypt
import secrets
from flask_login import UserMixin


logger = logging.getLogger(__name__)

db = SQLAlchemy()


def convert_to_tz(dt):
    """Return datetime as ISO string for display.
    If datetime is naive, assume it's UTC.
    Timezone conversion is handled in API routes.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()

class ScanResult(db.Model):
    __tablename__ = 'scan_results'
    
    # Configuration for output rotation (can be overridden via environment)
    MAX_OUTPUT_SIZE = int(os.getenv('MAX_OUTPUT_SIZE', '10000'))  # characters
    OUTPUT_ROTATION_ENABLED = os.getenv('OUTPUT_ROTATION_ENABLED', 'true').lower() == 'true'
    
    id = db.Column(db.Integer, primary_key=True)
    file_path = db.Column(db.String(500), nullable=False, unique=True, index=True)
    file_size = db.Column(db.BigInteger, nullable=True)  # Allow NULL during discovery
    file_type = db.Column(db.String(50), nullable=True)  # Allow NULL during discovery
    creation_date = db.Column(db.DateTime, nullable=True)  # Allow NULL during discovery
    is_corrupted = db.Column(db.Boolean, nullable=True, default=None, index=True)  # NULL = not scanned yet
    corruption_details = db.Column(db.Text)
    scan_date = db.Column(db.DateTime, nullable=True, index=True)  # NULL = not scanned yet
    marked_as_good = db.Column(db.Boolean, nullable=False, default=False, index=True)
    scan_status = db.Column(db.String(20), nullable=True, default='pending', index=True)  # pending, scanning, completed, error
    discovered_date = db.Column(db.DateTime, nullable=True, default=None, index=True)  # When file was discovered
    
    # New fields for enhanced features
    file_hash = db.Column(db.String(64), nullable=True, index=True)  # SHA-256 hash for change detection
    last_modified = db.Column(db.DateTime, nullable=True, index=True)  # File system modification time
    last_integrity_check_date = db.Column(db.DateTime, nullable=True, index=True)  # Last time integrity check was run on this file
    scan_tool = db.Column(db.String(50), nullable=True)  # Tool used for detection (ffmpeg, imagemagick, pil)
    scan_duration = db.Column(db.Float, nullable=True)  # Time taken to scan in seconds
    scan_output = db.Column(db.Text)  # Full tool output for debugging
    has_warnings = db.Column(db.Boolean, nullable=False, default=False, index=True)  # Whether scan found warnings
    warning_details = db.Column(db.Text)  # Details of any warnings found
    
    # Fields expected by API but currently missing
    error_message = db.Column(db.Text, nullable=True)  # Error message from scan
    media_info = db.Column(db.Text, nullable=True)  # JSON string of media metadata
    file_exists = db.Column(db.Boolean, nullable=False, default=True, index=True)  # Whether file exists on disk
    
    # Legacy column kept for backward compatibility with older database schemas
    deep_scan = db.Column(db.Boolean, nullable=True, default=False, server_default='false')
    
    # Output rotation tracking
    output_rotation_enabled = db.Column(db.Boolean, nullable=True)  # Per-record rotation setting

    # Composite indexes for query optimization (P1 audit fix)
    # Context7: Use Index("idx_name", "col1", "col2") inline syntax
    __table_args__ = (
        db.Index('idx_status_corrupted', 'scan_status', 'is_corrupted'),
        db.Index('idx_scan_date_corrupted', 'scan_date', 'is_corrupted'),
        db.Index('idx_exists_status', 'file_exists', 'scan_status'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'file_path': self.file_path,
            'file_size': self.file_size,
            'file_type': self.file_type,
            'creation_date': convert_to_tz(self.creation_date),
            'is_corrupted': self.is_corrupted,
            'corruption_details': self.corruption_details,
            'scan_date': convert_to_tz(self.scan_date),
            'marked_as_good': self.marked_as_good,
            'scan_status': self.scan_status,
            'file_hash': self.file_hash,
            'last_modified': convert_to_tz(self.last_modified),
            'last_integrity_check_date': convert_to_tz(self.last_integrity_check_date),
            'scan_tool': self.scan_tool,
            'scan_duration': self.scan_duration,
            'scan_output': self.scan_output,
            'has_warnings': self.has_warnings,
            'warning_details': self.warning_details,
            'discovered_date': convert_to_tz(self.discovered_date),
            'error_message': self.error_message,
            'media_info': self.media_info,
            'file_exists': self.file_exists
        }
    
    def append_output(self, new_output):
        """
        Append output to scan_output field with rotation if enabled.
        Prevents unbounded memory growth during long scans.
        """
        if not new_output:
            return
        
        # Check if rotation is enabled (globally or per-record)
        rotation_enabled = self.output_rotation_enabled if self.output_rotation_enabled is not None else self.OUTPUT_ROTATION_ENABLED
        
        if not rotation_enabled:
            # No rotation - just append
            self.scan_output = (self.scan_output or '') + new_output
            return
        
        current_output = self.scan_output or ''
        combined_length = len(current_output) + len(new_output)
        
        if combined_length > self.MAX_OUTPUT_SIZE:
            # Need to rotate - keep last 80% of allowed size
            keep_size = int(self.MAX_OUTPUT_SIZE * 0.8)
            
            # Calculate how much of the old output to keep
            if len(new_output) >= keep_size:
                # New output is larger than what we want to keep
                # Just keep the end of the new output
                self.scan_output = '... [output rotated] ...\n' + new_output[-keep_size:]
            else:
                # Keep some old output plus all new output
                old_keep_size = keep_size - len(new_output)
                self.scan_output = '... [output rotated] ...\n' + current_output[-old_keep_size:] + new_output
            
            logger.debug(f"Rotated output for {self.file_path}: was {combined_length} chars, now {len(self.scan_output)} chars")
        else:
            # Still under limit - just append
            self.scan_output = current_output + new_output
    
    def __repr__(self):
        return f'<ScanResult {self.file_path}>'

class IgnoredErrorPattern(db.Model):
    __tablename__ = 'ignored_error_patterns'
    
    id = db.Column(db.Integer, primary_key=True)
    pattern = db.Column(db.String(200), nullable=False, unique=True)
    description = db.Column(db.String(500))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_date = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))  # Keep for backward compatibility
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'pattern': self.pattern,
            'description': self.description,
            'created_at': convert_to_tz(self.created_at),
            'created_date': self.created_date.isoformat() if self.created_date else None,
            'is_active': self.is_active
        }

class Exclusion(db.Model):
    __tablename__ = 'exclusions'
    
    id = db.Column(db.Integer, primary_key=True)
    exclusion_type = db.Column(db.String(20), nullable=False)  # 'path' or 'extension'
    value = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    
    __table_args__ = (
        db.UniqueConstraint('exclusion_type', 'value', name='_type_value_uc'),
    )
    
    def to_dict(self):
        return {
            'id': self.id,
            'type': self.exclusion_type,
            'value': self.value,
            'created_at': convert_to_tz(self.created_at),
            'is_active': self.is_active
        }


class ScanSchedule(db.Model):
    __tablename__ = 'scan_schedules'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    cron_expression = db.Column(db.String(50), nullable=False)
    scan_paths = db.Column(db.Text)  # JSON array of paths to scan
    scan_type = db.Column(db.String(20), nullable=False, default='normal')  # normal, orphan, file_changes
    force_rescan = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_run = db.Column(db.DateTime, nullable=True)
    next_run = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_date = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))  # Keep for backward compatibility
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'cron_expression': self.cron_expression,
            'scan_paths': json.loads(self.scan_paths) if self.scan_paths else [],
            'scan_type': self.scan_type,
            'force_rescan': self.force_rescan,
            'is_active': self.is_active,
            'last_run': convert_to_tz(self.last_run),
            'next_run': convert_to_tz(self.next_run),
            'created_at': convert_to_tz(self.created_at),
            'created_date': convert_to_tz(self.created_date),
            'has_healthcheck': self.healthcheck_config is not None,
            'healthcheck_active': self.healthcheck_config.is_active if self.healthcheck_config else False
        }

class HealthcheckConfig(db.Model):
    __tablename__ = 'healthcheck_configs'

    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('scan_schedules.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    healthcheck_url = db.Column(db.String(500), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    send_start_ping = db.Column(db.Boolean, nullable=False, default=True)
    send_success_ping = db.Column(db.Boolean, nullable=False, default=True)
    send_failure_ping = db.Column(db.Boolean, nullable=False, default=True)
    include_report_data = db.Column(db.Boolean, nullable=False, default=True)
    last_ping_status = db.Column(db.String(20), nullable=True)  # success, failure, timeout, null
    last_ping_time = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationship
    schedule = db.relationship('ScanSchedule', backref=db.backref('healthcheck_config', uselist=False, cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'schedule_id': self.schedule_id,
            'healthcheck_url': self.healthcheck_url,
            'is_active': self.is_active,
            'send_start_ping': self.send_start_ping,
            'send_success_ping': self.send_success_ping,
            'send_failure_ping': self.send_failure_ping,
            'include_report_data': self.include_report_data,
            'last_ping_status': self.last_ping_status,
            'last_ping_time': convert_to_tz(self.last_ping_time),
            'created_at': convert_to_tz(self.created_at),
            'updated_at': convert_to_tz(self.updated_at)
        }

class ScanConfiguration(db.Model):
    __tablename__ = 'scan_configurations'

    id = db.Column(db.Integer, primary_key=True)
    # Old structure for backward compatibility
    key = db.Column(db.String(50), nullable=True, unique=True)
    value = db.Column(db.Text, nullable=True)
    description = db.Column(db.String(200))
    updated_date = db.Column(db.DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))
    
    # New structure expected by API and repositories
    path = db.Column(db.String(500), nullable=True, unique=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    
    def to_dict(self):
        # Support both old and new structures
        if self.path is not None:
            # New path-based structure
            return {
                'id': self.id,
                'path': self.path,
                'is_active': self.is_active,
                'created_at': convert_to_tz(self.created_at)
            }
        else:
            # Old key-value structure
            return {
                'id': self.id,
                'key': self.key,
                'value': self.value,
                'description': self.description,
                'updated_date': convert_to_tz(self.updated_date)
            }

class ScanState(db.Model):
    __tablename__ = 'scan_state'
    
    id = db.Column(db.Integer, primary_key=True)
    scan_id = db.Column(db.String(64), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    phase = db.Column(db.String(20), nullable=False, default='idle')  # idle, discovering, adding, scanning, completed
    phase_number = db.Column(db.Integer, nullable=False, default=0)
    phase_current = db.Column(db.Integer, nullable=False, default=0)
    phase_total = db.Column(db.Integer, nullable=False, default=0)
    files_processed = db.Column(db.Integer, nullable=False, default=0)
    estimated_total = db.Column(db.Integer, nullable=False, default=0)
    discovery_count = db.Column(db.Integer, nullable=False, default=0)
    start_time = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    end_time = db.Column(db.DateTime, nullable=True)
    current_file = db.Column(db.String(500), nullable=True)
    progress_message = db.Column(db.String(1000), nullable=True)  # Increased from 200
    error_message = db.Column(db.String(1000), nullable=True)  # Increased from 500
    directories = db.Column(db.Text, nullable=True)  # JSON array of directories being scanned
    force_rescan = db.Column(db.Boolean, nullable=False, default=False)
    # P1 Celery task queue integration
    celery_task_id = db.Column(db.String(36), nullable=True, index=True)  # Celery task ID for monitoring
    # Resumable scan fields
    current_chunk_index = db.Column(db.Integer, nullable=False, default=0)
    total_chunks = db.Column(db.Integer, nullable=False, default=0)
    chunks_completed = db.Column(db.Text, nullable=True)  # JSON array of completed chunk IDs
    
    # Progress tracking
    last_update = db.Column(db.DateTime, nullable=True)  # Track last progress update for stuck scan detection
    
    # Scan statistics tracking
    num_workers = db.Column(db.Integer, nullable=False, default=1)  # Number of parallel workers used
    files_added = db.Column(db.Integer, nullable=False, default=0)  # New files added to database
    files_updated = db.Column(db.Integer, nullable=False, default=0)  # Existing files updated
    
    # TODO: Crash recovery tracking columns will be added after migration
    # crash_count = db.Column(db.Integer, nullable=True, default=None)
    # last_crash_time = db.Column(db.DateTime, nullable=True)
    
    def to_dict(self):
        # Import here to avoid circular imports
        from pixelprobe.utils.helpers import create_state_dict
        return create_state_dict(self, extra_fields=['estimated_total', 'discovery_count'])
    
    @staticmethod
    def get_or_create():
        """Get the most recent scan state or create new one if none exists"""
        try:
            # First check for active scan
            scan_state = ScanState.query.filter_by(is_active=True).first()
            if scan_state:
                return scan_state
                
            # If no active scan, get the most recent one
            scan_state = ScanState.query.order_by(ScanState.id.desc()).first()
            if scan_state:
                return scan_state
        except Exception:
            # Table might not exist, return a transient instance
            scan_state = None
            
        # Only create new if no scan state exists at all
        if not scan_state:
            scan_state = ScanState()
            try:
                db.session.add(scan_state)
                db.session.commit()
            except Exception:
                # If we can't commit (e.g., in tests), just return the transient instance
                db.session.rollback()
        return scan_state
    
    @staticmethod
    def create_new_scan(scan_id=None):
        """Create a new scan state record for starting a new scan

        Args:
            scan_id: Optional scan ID to use (e.g., 'scheduled_11' for scheduled scans).
                     If not provided, a UUID will be generated.
        """
        # First, ensure no other scans are active (clean up any stale active states)
        try:
            # Deactivate any existing active scans
            active_scans = ScanState.query.filter_by(is_active=True).all()
            for scan in active_scans:
                logger.warning(f"Deactivating stale active scan {scan.id} before creating new scan")
                scan.is_active = False
                scan.phase = 'interrupted' if scan.phase != 'completed' else 'completed'
            if active_scans:
                db.session.commit()
        except Exception as e:
            logger.error(f"Error cleaning up active scans: {e}")
            db.session.rollback()

        # If scan_id provided (e.g., scheduled scans), delete any existing record with same ID
        # This allows scheduled scans to run again without hitting unique constraint violation
        if scan_id:
            try:
                existing = ScanState.query.filter_by(scan_id=scan_id).first()
                if existing:
                    logger.info(f"Removing previous scan_state with scan_id={scan_id} (id={existing.id}, phase={existing.phase})")
                    db.session.delete(existing)
                    db.session.commit()
            except Exception as e:
                logger.error(f"Error removing existing scan_state with scan_id={scan_id}: {e}")
                db.session.rollback()

        # Always create a fresh scan state when starting a new scan
        scan_state = ScanState()
        # Use provided scan_id (for scheduled scans) or generate UUID
        scan_state.scan_id = scan_id if scan_id else str(uuid.uuid4())
        scan_state.is_active = False  # Will be set to True when scan actually starts
        scan_state.phase = 'idle'
        try:
            db.session.add(scan_state)
            db.session.commit()
            # Refresh the object to reattach it to the session after commit
            db.session.refresh(scan_state)
            logger.info(f"Created new scan state with ID: {scan_state.id}, scan_id: {scan_state.scan_id}")
        except Exception as e:
            logger.error(f"Failed to create new scan state: {e}")
            db.session.rollback()
            raise
        return scan_state
    
    def start_scan(self, directories, force_rescan=False):
        """Start a new scan"""
        self.phase = 'discovering'
        self.is_active = True  # Ensure scan is marked as active
        self.start_time = datetime.now(timezone.utc)
        self.last_update = datetime.now(timezone.utc)  # Initialize last_update
        self.end_time = None  # Clear any previous end time
        self.directories = json.dumps(directories) if isinstance(directories, list) else directories
        self.force_rescan = force_rescan
        self.files_processed = 0
        self.estimated_total = 0
        self.current_file = None
        self.error_message = None
        db.session.commit()
        logger.info(f"Scan started: directories={directories}, "
                    f"force_rescan={force_rescan}")
    
    def cancel_scan(self):
        """Cancel the current scan"""
        self.phase = 'cancelled'
        self.is_active = False
        self.end_time = datetime.now(timezone.utc)
        db.session.commit()
    
    def error_scan(self, error_message):
        """Mark scan as errored"""
        self.phase = 'error'
        self.error_message = error_message
        self.is_active = False
        self.end_time = datetime.now(timezone.utc)
        db.session.commit()
    
    def update_progress(self, files_processed, total_files, phase=None, current_file=None):
        """Update scan progress with safer transaction handling"""
        try:
            self.files_processed = files_processed

            # CRITICAL: Prevent "x of 0" display issue
            # Never allow estimated_total to be set to 0 if we already have a positive value
            # This can happen due to race conditions between workers
            if total_files == 0 and self.estimated_total > 0 and self.phase not in ['idle', 'discovering']:
                logger.debug(f"Keeping estimated_total={self.estimated_total} (not overwriting with 0)")
            else:
                self.estimated_total = total_files

            # Also protect phase_total from being reset to 0
            # This applies to all phases except idle and discovering
            if total_files == 0 and self.phase_total > 0 and self.phase not in ['idle', 'discovering']:
                logger.debug(f"Keeping phase_total={self.phase_total} in phase '{self.phase}' (not overwriting with 0)")
            else:
                self.phase_total = total_files

            self.phase_current = files_processed
            
            # Update last_update timestamp for stuck scan detection
            self.last_update = datetime.now(timezone.utc)
            
            # Handle phase transitions explicitly
            if phase:
                self.phase = phase
                logger.info(f"Scan phase updated to: {phase}")
            elif total_files > 0 and self.phase in ['idle', 'discovering']:
                # Auto-transition to scanning if we have files to process
                self.phase = 'scanning'
                logger.info(f"Auto-transitioned scan phase to: scanning "
                           f"(files_processed={files_processed}, total={total_files})")
                
            if current_file is not None:  # Allow empty string to clear current file
                self.current_file = current_file
                
            # Attempt to commit changes with retry logic
            for retry in range(3):
                try:
                    db.session.commit()
                    break
                except Exception as e:
                    logger.warning(f"Failed to update scan progress (attempt {retry + 1}/3): {e}")
                    db.session.rollback()
                    if retry == 2:
                        logger.error(f"Failed to update scan progress after 3 attempts: {e}")
                        raise
        except Exception as e:
            logger.error(f"Error in update_progress: {e}")
            try:
                db.session.rollback()
            except:
                pass
        
            # Ensure the scan is marked as active when we have actual progress
            # Check if instance is attached to session first
            try:
                current_phase = self.phase
                if current_phase in ['discovering', 'adding', 'scanning'] and total_files > 0:
                    self.is_active = True
            except Exception as e:
                logger.warning(f"Could not access scan state phase, re-attaching to session: {e}")
                # Try to re-attach to session
                try:
                    self = db.session.merge(self)
                    if self.phase in ['discovering', 'adding', 'scanning'] and total_files > 0:
                        self.is_active = True
                except Exception as re_attach_error:
                    logger.error(f"Failed to re-attach scan state to session: {re_attach_error}")
        
        try:
            db.session.commit()
            logger.debug(f"Progress updated: {files_processed}/{total_files}, "
                        f"phase={self.phase}, file={current_file}")
        except Exception as e:
            logger.error(f"Failed to commit progress update: {e}")
            db.session.rollback()
            raise
    
    def complete_scan(self):
        """Mark scan as completed - thread-safe version

        Also cleans up any orphaned pending files that were discovered
        during this scan but never actually scanned.
        """
        try:
            # Get the scan ID before we lose session binding
            scan_id = self.id if hasattr(self, 'id') and self.id else 'unknown'
            end_time = datetime.now(timezone.utc)
            start_time = self.start_time if hasattr(self, 'start_time') else None

            # Update the database record directly using thread-safe query
            # This avoids the detached instance problem
            from sqlalchemy import text
            db.session.execute(
                text("UPDATE scan_state SET phase = 'completed', is_active = false, end_time = :end_time WHERE id = :id"),
                {'end_time': end_time, 'id': scan_id}
            )

            # Clean up orphaned pending files that were discovered during this scan
            # but never actually scanned (likely due to filters or exclusions)
            if start_time:
                orphaned_result = db.session.execute(
                    text("""
                        UPDATE scan_results
                        SET scan_status = 'skipped',
                            scan_output = 'File discovered but not scanned (likely filtered or excluded)'
                        WHERE scan_status = 'pending'
                        AND discovered_date >= :start_time
                        AND discovered_date <= :end_time
                    """),
                    {'start_time': start_time, 'end_time': end_time}
                )
                orphaned_count = orphaned_result.rowcount
                if orphaned_count > 0:
                    logger.info(f"Scan {scan_id}: Cleaned up {orphaned_count} orphaned pending files")

            db.session.commit()
            logger.info(f"Scan {scan_id} completed - phase set to 'completed', is_active=False")
        except Exception as e:
            logger.error(f"Failed to commit scan completion: {e}")
            db.session.rollback()
            raise

class CleanupState(db.Model):
    __tablename__ = 'cleanup_state'
    
    id = db.Column(db.Integer, primary_key=True)
    cleanup_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    phase = db.Column(db.String(20), nullable=False, default='idle')  # idle, checking, deleting, complete, error, cancelled
    phase_number = db.Column(db.Integer, nullable=False, default=1)
    phase_current = db.Column(db.Integer, nullable=False, default=0)
    phase_total = db.Column(db.Integer, nullable=False, default=0)
    files_processed = db.Column(db.Integer, nullable=False, default=0)
    total_files = db.Column(db.Integer, nullable=False, default=0)
    orphaned_found = db.Column(db.Integer, nullable=False, default=0)
    start_time = db.Column(db.DateTime(timezone=True), nullable=True)
    end_time = db.Column(db.DateTime(timezone=True), nullable=True)
    current_file = db.Column(db.String(500), nullable=True)
    progress_message = db.Column(db.String(1000), nullable=True)  # Increased from 200
    error_message = db.Column(db.String(1000), nullable=True)  # Increased from 500
    cancel_requested = db.Column(db.Boolean, nullable=True, default=False)
    
    def to_dict(self):
        # Import here to avoid circular imports
        from pixelprobe.utils.helpers import create_state_dict
        return create_state_dict(self, extra_fields=['orphaned_found'])

class FileChangesState(db.Model):
    __tablename__ = 'file_changes_state'
    
    id = db.Column(db.Integer, primary_key=True)
    check_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    phase = db.Column(db.String(20), nullable=False, default='idle')  # idle, starting, checking_hashes, verifying_changes, complete, error, cancelled
    phase_number = db.Column(db.Integer, nullable=False, default=1)
    phase_current = db.Column(db.Integer, nullable=False, default=0)
    phase_total = db.Column(db.Integer, nullable=False, default=0)
    files_processed = db.Column(db.Integer, nullable=False, default=0)
    total_files = db.Column(db.Integer, nullable=False, default=0)
    changes_found = db.Column(db.Integer, nullable=False, default=0)
    corrupted_found = db.Column(db.Integer, nullable=False, default=0)
    start_time = db.Column(db.DateTime(timezone=True), nullable=True)
    end_time = db.Column(db.DateTime(timezone=True), nullable=True)
    last_heartbeat = db.Column(db.DateTime(timezone=True), nullable=True)  # Track liveness to detect stuck workers
    current_file = db.Column(db.String(500), nullable=True)
    progress_message = db.Column(db.String(1000), nullable=True)  # Increased from 200
    error_message = db.Column(db.String(1000), nullable=True)  # Increased from 500
    changed_files = db.Column(db.Text, nullable=True)  # JSON list of changed files
    cancel_requested = db.Column(db.Boolean, nullable=True, default=False)
    
    def to_dict(self):
        # Import here to avoid circular imports
        from pixelprobe.utils.helpers import create_state_dict
        result = create_state_dict(self, extra_fields=['changes_found', 'corrupted_found'])
        # Handle special case for changed_files JSON field
        result['changed_files'] = json.loads(self.changed_files) if self.changed_files else []
        return result

class ScanChunk(db.Model):
    __tablename__ = 'scan_chunks'
    
    id = db.Column(db.Integer, primary_key=True)
    scan_id = db.Column(db.String(64), nullable=False, index=True)
    chunk_id = db.Column(db.String(100), nullable=False, index=True)  # Removed unique constraint
    directory_path = db.Column(db.String(500), nullable=False)
    phase = db.Column(db.String(20), nullable=False, default='scanning')  # discovering, adding, scanning
    status = db.Column(db.String(20), nullable=False, default='pending')  # pending, processing, completed, error
    files_discovered = db.Column(db.Integer, nullable=False, default=0)
    files_added = db.Column(db.Integer, nullable=False, default=0)
    files_scanned = db.Column(db.Integer, nullable=False, default=0)
    files_processed = db.Column(db.Integer, nullable=False, default=0)
    is_complete = db.Column(db.Boolean, nullable=False, default=False)
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    celery_task_id = db.Column(db.String(36), nullable=True, index=True)  # Celery task ID for chunk processing
    
    # Add composite unique constraint
    __table_args__ = (db.UniqueConstraint('scan_id', 'chunk_id', name='uq_scan_chunks_scan_chunk'),)
    
    def to_dict(self):
        return {
            'id': self.id,
            'scan_id': self.scan_id,
            'chunk_id': self.chunk_id,
            'directory_path': self.directory_path,
            'phase': self.phase,
            'status': self.status,
            'files_discovered': self.files_discovered,
            'files_added': self.files_added,
            'files_scanned': self.files_scanned,
            'start_time': convert_to_tz(self.start_time),
            'end_time': convert_to_tz(self.end_time),
            'error_message': self.error_message
        }

class ScanReport(db.Model):
    __tablename__ = 'scan_reports'
    
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    scan_type = db.Column(db.String(50), nullable=False)  # full_scan, rescan, cleanup, file_changes
    start_time = db.Column(db.DateTime(timezone=True), nullable=False)
    end_time = db.Column(db.DateTime(timezone=True), nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)
    
    # Scan parameters
    directories_scanned = db.Column(db.Text, nullable=True)  # JSON list of directories
    force_rescan = db.Column(db.Boolean, nullable=False, default=False)
    num_workers = db.Column(db.Integer, nullable=False, default=1)
    
    # File statistics
    total_files_discovered = db.Column(db.Integer, nullable=False, default=0)
    files_scanned = db.Column(db.Integer, nullable=False, default=0)
    files_added = db.Column(db.Integer, nullable=False, default=0)
    files_updated = db.Column(db.Integer, nullable=False, default=0)
    files_corrupted = db.Column(db.Integer, nullable=False, default=0)
    files_with_warnings = db.Column(db.Integer, nullable=False, default=0)
    files_error = db.Column(db.Integer, nullable=False, default=0)
    
    # Cleanup statistics (for cleanup operations)
    orphaned_records_found = db.Column(db.Integer, nullable=False, default=0)
    orphaned_records_deleted = db.Column(db.Integer, nullable=False, default=0)
    
    # File changes statistics (for file_changes operations)
    files_changed = db.Column(db.Integer, nullable=False, default=0)
    files_corrupted_new = db.Column(db.Integer, nullable=False, default=0)
    
    # Scan status
    status = db.Column(db.String(20), nullable=False, default='running')  # running, completed, error, cancelled
    error_message = db.Column(db.Text, nullable=True)
    
    # Additional metadata
    scan_id = db.Column(db.String(64), nullable=True)  # Link to ScanState scan_id
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    
    def to_dict(self):
        return {
            'id': self.id,
            'report_id': self.report_id,
            'scan_type': self.scan_type,
            'start_time': convert_to_tz(self.start_time),
            'end_time': convert_to_tz(self.end_time),
            'duration_seconds': self.duration_seconds,
            'directories_scanned': json.loads(self.directories_scanned) if self.directories_scanned else [],
            'force_rescan': self.force_rescan,
            'num_workers': self.num_workers,
            'total_files_discovered': self.total_files_discovered,
            'files_scanned': self.files_scanned,
            'files_added': self.files_added,
            'files_updated': self.files_updated,
            'files_corrupted': self.files_corrupted,
            'files_with_warnings': self.files_with_warnings,
            'files_error': self.files_error,
            'orphaned_records_found': self.orphaned_records_found,
            'orphaned_records_deleted': self.orphaned_records_deleted,
            'files_changed': self.files_changed,
            'files_corrupted_new': self.files_corrupted_new,
            'status': self.status,
            'error_message': self.error_message,
            'scan_id': self.scan_id,
            'created_at': convert_to_tz(self.created_at)
        }


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=True)  # All users have admin access
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime(timezone=True), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    first_setup_required = db.Column(db.Boolean, nullable=False, default=False)

    # Relationship to API tokens
    api_tokens = db.relationship('APIToken', back_populates='user', cascade='all, delete-orphan')

    def set_password(self, password):
        """Hash and set the user's password"""
        self.password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    def check_password(self, password):
        """Check if the provided password matches the hash"""
        if not self.password_hash:
            return False
        return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))

    def generate_api_token(self, description=None):
        """Generate a new API token for this user"""
        token = APIToken(
            user_id=self.id,
            description=description
        )
        db.session.add(token)
        return token

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'is_admin': self.is_admin,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'is_active': self.is_active,
            'first_setup_required': self.first_setup_required,
            'api_tokens_count': len(self.api_tokens)
        }

    def __repr__(self):
        return f'<User {self.username}>'


class APIToken(db.Model):
    __tablename__ = 'api_tokens'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    description = db.Column(db.String(200))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_used = db.Column(db.DateTime(timezone=True), nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    # Relationship to user
    user = db.relationship('User', back_populates='api_tokens')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.token:
            self.token = secrets.token_urlsafe(48)

    def is_valid(self):
        """Check if the token is valid (active and not expired)"""
        if not self.is_active:
            return False
        if self.expires_at:
            # Ensure expires_at is timezone-aware for comparison
            expires_at = self.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                return False
        return True

    def update_last_used(self):
        """Update the last_used timestamp"""
        self.last_used = datetime.now(timezone.utc)
        db.session.commit()

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_used': self.last_used.isoformat() if self.last_used else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'is_active': self.is_active,
            'token_preview': f"{self.token[:8]}..." if self.token else None
        }

    def __repr__(self):
        return f'<APIToken {self.id} for user {self.user_id}>'


class LogEntry(db.Model):
    """Persistent log storage for the View Logs feature"""
    __tablename__ = 'log_entries'

    id = db.Column(db.Integer, primary_key=True)
    scan_id = db.Column(db.String(64), nullable=True)
    celery_task_id = db.Column(db.String(64), nullable=True)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    level = db.Column(db.String(10), nullable=False)
    logger_name = db.Column(db.String(200), nullable=True)
    message = db.Column(db.Text, nullable=False)
    traceback = db.Column(db.Text, nullable=True)

    # Indexes are created by migration (pixelprobe/migrations/startup.py) to avoid duplication

    def to_dict(self):
        return {
            'id': self.id,
            'scan_id': self.scan_id,
            'celery_task_id': self.celery_task_id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'level': self.level,
            'logger_name': self.logger_name,
            'message': self.message,
            'traceback': self.traceback
        }


class AppConfig(db.Model):
    """Application-level configuration stored in the database"""
    __tablename__ = 'app_configs'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)
    description = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'key': self.key,
            'value': self.value,
            'description': self.description,
            'created_at': convert_to_tz(self.created_at),
            'updated_at': convert_to_tz(self.updated_at)
        }


class NotificationProvider(db.Model):
    """Notification provider configuration (Pushover, ntfy, webhooks)"""
    __tablename__ = 'notification_providers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    provider_type = db.Column(db.String(20), nullable=False, index=True)  # 'pushover', 'ntfy', 'webhook'
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    configuration = db.Column(db.JSON, nullable=False)  # Provider-specific config
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, 
                          default=lambda: datetime.now(timezone.utc), 
                          onupdate=lambda: datetime.now(timezone.utc))
    last_notification_status = db.Column(db.String(20), nullable=True)  # 'success', 'failure'
    last_notification_time = db.Column(db.DateTime(timezone=True), nullable=True)

    # Relationship to notification rules
    rules = db.relationship('NotificationRule', back_populates='provider', cascade='all, delete-orphan')

    def to_dict(self, include_config=False):
        result = {
            'id': self.id,
            'name': self.name,
            'provider_type': self.provider_type,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_notification_status': self.last_notification_status,
            'last_notification_time': self.last_notification_time.isoformat() if self.last_notification_time else None,
            'rules_count': len(self.rules)
        }

        if include_config:
            # Mask sensitive data in config
            masked_config = self.configuration.copy()
            if self.provider_type == 'pushover':
                if 'api_token' in masked_config:
                    masked_config['api_token'] = '***'
                if 'user_key' in masked_config:
                    masked_config['user_key'] = '***'
            elif self.provider_type == 'ntfy':
                if 'token' in masked_config:
                    masked_config['token'] = '***'
            elif self.provider_type == 'webhook':
                if 'webhook_url' in masked_config:
                    # Show only the domain
                    from urllib.parse import urlparse
                    parsed = urlparse(masked_config['webhook_url'])
                    masked_config['webhook_url'] = f"{parsed.scheme}://{parsed.netloc}/***"

            result['configuration'] = masked_config

        return result

    def __repr__(self):
        return f'<NotificationProvider {self.name} ({self.provider_type})>'


class NotificationRule(db.Model):
    """Rules for when to send notifications"""
    __tablename__ = 'notification_rules'

    id = db.Column(db.Integer, primary_key=True)
    provider_id = db.Column(db.Integer, db.ForeignKey('notification_providers.id', ondelete='CASCADE'), 
                           nullable=False, index=True)
    event_type = db.Column(db.String(50), nullable=False, index=True)
    # Event types: 'scan_start', 'scan_complete', 'scan_failed', 'scan_missed',
    #              'user_added', 'user_deleted', 'api_key_added', 'api_key_deleted', 'auth_failed'
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    priority = db.Column(db.String(10), nullable=False, default='normal')  # 'low', 'normal', 'high'
    conditions = db.Column(db.JSON, nullable=True)  # Optional conditions (e.g., {"corrupted_count": ">0"})
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, 
                          default=lambda: datetime.now(timezone.utc), 
                          onupdate=lambda: datetime.now(timezone.utc))

    # Relationship to provider
    provider = db.relationship('NotificationProvider', back_populates='rules')

    def to_dict(self):
        return {
            'id': self.id,
            'provider_id': self.provider_id,
            'provider_name': self.provider.name if self.provider else None,
            'event_type': self.event_type,
            'is_active': self.is_active,
            'priority': self.priority,
            'conditions': self.conditions,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def __repr__(self):
        return f'<NotificationRule {self.event_type} -> Provider {self.provider_id}>'

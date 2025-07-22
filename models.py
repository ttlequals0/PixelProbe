from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
import json
import uuid
import logging


logger = logging.getLogger(__name__)

db = SQLAlchemy()

# Import shared utilities after models are loaded
# This will be imported in app.py to avoid circular imports

class ScanResult(db.Model):
    __tablename__ = 'scan_results'
    
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
    scan_tool = db.Column(db.String(50), nullable=True)  # Tool used for detection (ffmpeg, imagemagick, pil)
    scan_duration = db.Column(db.Float, nullable=True)  # Time taken to scan in seconds
    scan_output = db.Column(db.Text)  # Full tool output for debugging
    has_warnings = db.Column(db.Boolean, nullable=False, default=False, index=True)  # Whether scan found warnings
    warning_details = db.Column(db.Text)  # Details of any warnings found
    deep_scan = db.Column(db.Boolean, nullable=False, default=False)  # Whether deep scan is requested
    
    # Fields expected by API but currently missing
    error_message = db.Column(db.Text, nullable=True)  # Error message from scan
    media_info = db.Column(db.Text, nullable=True)  # JSON string of media metadata
    file_exists = db.Column(db.Boolean, nullable=False, default=True, index=True)  # Whether file exists on disk
    
    def to_dict(self):
        return {
            'id': self.id,
            'file_path': self.file_path,
            'file_size': self.file_size,
            'file_type': self.file_type,
            'creation_date': self.creation_date.isoformat() if self.creation_date else None,
            'is_corrupted': self.is_corrupted,
            'corruption_details': self.corruption_details,
            'scan_date': self.scan_date.isoformat() if self.scan_date else None,
            'marked_as_good': self.marked_as_good,
            'scan_status': self.scan_status,
            'file_hash': self.file_hash,
            'last_modified': self.last_modified.isoformat() if self.last_modified else None,
            'scan_tool': self.scan_tool,
            'scan_duration': self.scan_duration,
            'scan_output': self.scan_output,
            'has_warnings': self.has_warnings,
            'warning_details': self.warning_details,
            'deep_scan': self.deep_scan,
            'discovered_date': self.discovered_date.isoformat() if self.discovered_date else None,
            'error_message': self.error_message,
            'media_info': self.media_info,
            'file_exists': self.file_exists
        }
    
    def __repr__(self):
        return f'<ScanResult {self.file_path}>'

class IgnoredErrorPattern(db.Model):
    __tablename__ = 'ignored_error_patterns'
    
    id = db.Column(db.Integer, primary_key=True)
    pattern = db.Column(db.String(200), nullable=False, unique=True)
    description = db.Column(db.String(500))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)  # Keep for backward compatibility
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'pattern': self.pattern,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'created_date': self.created_date.isoformat() if self.created_date else None,
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
    created_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)  # Keep for backward compatibility
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'cron_expression': self.cron_expression,
            'scan_paths': self.scan_paths,
            'scan_type': self.scan_type,
            'force_rescan': self.force_rescan,
            'is_active': self.is_active,
            'last_run': self.last_run.isoformat() if self.last_run else None,
            'next_run': self.next_run.isoformat() if self.next_run else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'created_date': self.created_date.isoformat() if self.created_date else None
        }

class ScanConfiguration(db.Model):
    __tablename__ = 'scan_configurations'
    
    id = db.Column(db.Integer, primary_key=True)
    # Old structure for backward compatibility
    key = db.Column(db.String(50), nullable=True, unique=True)
    value = db.Column(db.Text, nullable=True)
    description = db.Column(db.String(200))
    updated_date = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    
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
                'created_at': self.created_at.isoformat() if self.created_at else None
            }
        else:
            # Old key-value structure
            return {
                'id': self.id,
                'key': self.key,
                'value': self.value,
                'description': self.description,
                'updated_date': self.updated_date.isoformat() if self.updated_date else None
            }

class ScanState(db.Model):
    __tablename__ = 'scan_state'
    
    id = db.Column(db.Integer, primary_key=True)
    scan_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    phase = db.Column(db.String(20), nullable=False, default='idle')  # idle, discovering, adding, scanning, completed
    phase_number = db.Column(db.Integer, nullable=False, default=0)
    phase_current = db.Column(db.Integer, nullable=False, default=0)
    phase_total = db.Column(db.Integer, nullable=False, default=0)
    files_processed = db.Column(db.Integer, nullable=False, default=0)
    estimated_total = db.Column(db.Integer, nullable=False, default=0)
    discovery_count = db.Column(db.Integer, nullable=False, default=0)
    start_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    end_time = db.Column(db.DateTime, nullable=True)
    current_file = db.Column(db.String(500), nullable=True)
    progress_message = db.Column(db.String(200), nullable=True)
    error_message = db.Column(db.String(500), nullable=True)
    directories = db.Column(db.Text, nullable=True)  # JSON array of directories being scanned
    force_rescan = db.Column(db.Boolean, nullable=False, default=False)
    
    def to_dict(self):
        # Import here to avoid circular imports
        from utils import create_state_dict
        return create_state_dict(self, extra_fields=['estimated_total', 'discovery_count'])
    
    @staticmethod
    def get_or_create():
        """Get existing active scan state or create new one"""
        try:
            scan_state = ScanState.query.filter_by(is_active=True).first()
        except Exception:
            # Table might not exist, return a transient instance
            scan_state = None
            
        if not scan_state:
            scan_state = ScanState()
            try:
                db.session.add(scan_state)
                db.session.commit()
            except Exception:
                # If we can't commit (e.g., in tests), just return the transient instance
                db.session.rollback()
        return scan_state
    
    def start_scan(self, directories, force_rescan=False):
        """Start a new scan"""
        self.phase = 'discovering'
        self.is_active = True  # Ensure scan is marked as active
        self.start_time = datetime.now(timezone.utc)
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
        """Update scan progress"""
        self.files_processed = files_processed
        self.estimated_total = total_files
        
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
        
        # Ensure the scan is marked as active when we have actual progress
        if self.phase in ['discovering', 'adding', 'scanning'] and total_files > 0:
            self.is_active = True
        
        try:
            db.session.commit()
            logger.debug(f"Progress updated: {files_processed}/{total_files}, "
                        f"phase={self.phase}, file={current_file}")
        except Exception as e:
            logger.error(f"Failed to commit progress update: {e}")
            db.session.rollback()
            raise
    
    def complete_scan(self):
        """Mark scan as completed"""
        self.phase = 'completed'
        self.is_active = False  # Mark as inactive when completed
        self.end_time = datetime.now(timezone.utc)
        
        # Force commit with explicit session management for threading
        try:
            db.session.commit()
            logger.info(f"Scan {self.id} completed - phase set to 'completed', is_active=False")
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
    progress_message = db.Column(db.String(200), nullable=True)
    error_message = db.Column(db.String(500), nullable=True)
    
    def to_dict(self):
        # Import here to avoid circular imports
        from utils import create_state_dict
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
    current_file = db.Column(db.String(500), nullable=True)
    progress_message = db.Column(db.String(200), nullable=True)
    error_message = db.Column(db.String(500), nullable=True)
    changed_files = db.Column(db.Text, nullable=True)  # JSON list of changed files
    
    def to_dict(self):
        # Import here to avoid circular imports
        from utils import create_state_dict
        result = create_state_dict(self, extra_fields=['changes_found', 'corrupted_found'])
        # Handle special case for changed_files JSON field
        result['changed_files'] = json.loads(self.changed_files) if self.changed_files else []
        return result

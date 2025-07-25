from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
import json
import uuid

db = SQLAlchemy()

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
            'deep_scan': self.deep_scan
        }
    
    def __repr__(self):
        return f'<ScanResult {self.file_path}>'

class IgnoredErrorPattern(db.Model):
    __tablename__ = 'ignored_error_patterns'
    
    id = db.Column(db.Integer, primary_key=True)
    pattern = db.Column(db.String(200), nullable=False, unique=True)
    description = db.Column(db.String(500))
    created_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'pattern': self.pattern,
            'description': self.description,
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
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_run = db.Column(db.DateTime, nullable=True)
    next_run = db.Column(db.DateTime, nullable=True)
    created_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'cron_expression': self.cron_expression,
            'scan_paths': self.scan_paths,
            'scan_type': self.scan_type,
            'is_active': self.is_active,
            'last_run': self.last_run.isoformat() if self.last_run else None,
            'next_run': self.next_run.isoformat() if self.next_run else None,
            'created_date': self.created_date.isoformat() if self.created_date else None
        }

class ScanConfiguration(db.Model):
    __tablename__ = 'scan_configurations'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), nullable=False, unique=True)
    value = db.Column(db.Text, nullable=False)
    description = db.Column(db.String(200))
    updated_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    def to_dict(self):
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
    
    def to_dict(self):
        return {
            'id': self.id,
            'scan_id': self.scan_id,
            'is_active': self.is_active,
            'phase': self.phase,
            'phase_number': self.phase_number,
            'phase_current': self.phase_current,
            'phase_total': self.phase_total,
            'files_processed': self.files_processed,
            'estimated_total': self.estimated_total,
            'discovery_count': self.discovery_count,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'current_file': self.current_file,
            'progress_message': self.progress_message,
            'error_message': self.error_message
        }

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
        return {
            'id': self.id,
            'cleanup_id': self.cleanup_id,
            'is_active': self.is_active,
            'phase': self.phase,
            'phase_number': self.phase_number,
            'phase_current': self.phase_current,
            'phase_total': self.phase_total,
            'files_processed': self.files_processed,
            'total_files': self.total_files,
            'orphaned_found': self.orphaned_found,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'current_file': self.current_file,
            'progress_message': self.progress_message,
            'error_message': self.error_message
        }

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
        return {
            'id': self.id,
            'check_id': self.check_id,
            'is_active': self.is_active,
            'phase': self.phase,
            'phase_number': self.phase_number,
            'phase_current': self.phase_current,
            'phase_total': self.phase_total,
            'files_processed': self.files_processed,
            'total_files': self.total_files,
            'changes_found': self.changes_found,
            'corrupted_found': self.corrupted_found,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'current_file': self.current_file,
            'progress_message': self.progress_message,
            'error_message': self.error_message,
            'changed_files': json.loads(self.changed_files) if self.changed_files else []
        }
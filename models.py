from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

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
    ignored_error_types = db.Column(db.Text)  # JSON array of ignored error patterns
    
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
            'marked_as_good': getattr(self, 'marked_as_good', False),
            'scan_status': getattr(self, 'scan_status', 'completed'),
            'discovered_date': getattr(self, 'discovered_date', self.scan_date).isoformat() if getattr(self, 'discovered_date', self.scan_date) else None,
            'file_hash': getattr(self, 'file_hash', None),
            'last_modified': getattr(self, 'last_modified', None).isoformat() if getattr(self, 'last_modified', None) else None,
            'scan_tool': getattr(self, 'scan_tool', None),
            'scan_duration': getattr(self, 'scan_duration', None),
            'scan_output': getattr(self, 'scan_output', None),
            'ignored_error_types': getattr(self, 'ignored_error_types', None)
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
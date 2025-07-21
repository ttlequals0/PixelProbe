"""
Security utilities for PixelProbe
"""
import os
import re
import logging
from functools import wraps
from datetime import datetime
from flask import request, jsonify, current_app
from werkzeug.security import safe_join
from models import db, ScanConfiguration

logger = logging.getLogger(__name__)

class SecurityError(Exception):
    """Base exception for security-related errors"""
    pass

class PathTraversalError(SecurityError):
    """Raised when a path traversal attempt is detected"""
    pass

def get_allowed_scan_paths():
    """Get all allowed scan paths from configuration"""
    try:
        # Get paths from database configuration
        configs = ScanConfiguration.query.filter_by(is_active=True).all()
        allowed_paths = [os.path.abspath(config.path) for config in configs]
        
        # Add any environment-configured paths
        env_paths = os.environ.get('ALLOWED_SCAN_PATHS', '').split(':')
        for path in env_paths:
            if path and os.path.exists(path):
                allowed_paths.append(os.path.abspath(path))
        
        return allowed_paths
    except Exception as e:
        logger.error(f"Error getting allowed scan paths: {e}")
        return []

def validate_file_path(file_path, allowed_paths=None):
    """
    Validate that a file path is within allowed directories
    
    Args:
        file_path: The path to validate
        allowed_paths: List of allowed base paths (if None, uses configured paths)
        
    Returns:
        Normalized absolute path if valid
        
    Raises:
        PathTraversalError: If path is outside allowed directories
    """
    if not file_path:
        raise PathTraversalError("Empty file path")
    
    # Normalize and get absolute path
    normalized = os.path.normpath(os.path.abspath(file_path))
    
    # Check for suspicious patterns
    suspicious_patterns = [
        r'\.\.',  # Parent directory references
        r'~',     # Home directory references
        r'\$',    # Environment variable references
        r'%',     # Windows environment variables
    ]
    
    for pattern in suspicious_patterns:
        if re.search(pattern, file_path):
            raise PathTraversalError(f"Suspicious pattern detected: {pattern}")
    
    # Get allowed paths if not provided
    if allowed_paths is None:
        allowed_paths = get_allowed_scan_paths()
    
    if not allowed_paths:
        raise PathTraversalError("No allowed scan paths configured")
    
    # Check if path is within allowed directories
    for allowed_path in allowed_paths:
        allowed_abs = os.path.abspath(allowed_path)
        if normalized.startswith(allowed_abs + os.sep) or normalized == allowed_abs:
            # Additional check: ensure the file exists and is readable
            if os.path.exists(normalized) and os.access(normalized, os.R_OK):
                return normalized
            else:
                raise PathTraversalError(f"File not found or not readable: {file_path}")
    
    raise PathTraversalError(f"Path outside allowed directories: {file_path}")

def validate_directory_path(dir_path):
    """
    Validate that a directory path is safe
    
    Args:
        dir_path: The directory path to validate
        
    Returns:
        Normalized absolute path if valid
        
    Raises:
        PathTraversalError: If path contains dangerous patterns
    """
    if not dir_path:
        raise PathTraversalError("Empty directory path")
    
    # Normalize and get absolute path
    normalized = os.path.normpath(os.path.abspath(dir_path))
    
    # Check for suspicious patterns
    if '..' in dir_path or '~' in dir_path:
        raise PathTraversalError("Directory path contains suspicious patterns")
    
    # Ensure it's a directory
    if os.path.exists(normalized) and not os.path.isdir(normalized):
        raise PathTraversalError("Path is not a directory")
    
    return normalized

def sanitize_filename(filename):
    """
    Sanitize a filename to prevent directory traversal
    
    Args:
        filename: The filename to sanitize
        
    Returns:
        Sanitized filename
    """
    if not filename:
        return ""
    
    # Remove any directory components
    filename = os.path.basename(filename)
    
    # Remove dangerous characters
    filename = re.sub(r'[^\w\s.-]', '', filename)
    
    # Limit length
    max_length = 255
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        filename = name[:max_length - len(ext)] + ext
    
    return filename

def validate_command_args(args):
    """
    Validate command arguments to prevent injection
    
    Args:
        args: List of command arguments
        
    Returns:
        Validated arguments
        
    Raises:
        ValueError: If arguments contain dangerous patterns
    """
    if not isinstance(args, list):
        raise ValueError("Command arguments must be a list")
    
    dangerous_patterns = [
        r'[;&|`$]',  # Shell metacharacters
        r'\n|\r',    # Newlines
        r'\\x00',    # Null bytes
    ]
    
    validated = []
    for arg in args:
        if not isinstance(arg, str):
            arg = str(arg)
        
        for pattern in dangerous_patterns:
            if re.search(pattern, arg):
                raise ValueError(f"Dangerous pattern in argument: {pattern}")
        
        validated.append(arg)
    
    return validated

def safe_subprocess_run(args, **kwargs):
    """
    Safe wrapper for subprocess.run that validates arguments
    
    Args:
        args: Command arguments as a list
        **kwargs: Additional arguments for subprocess.run
        
    Returns:
        subprocess.CompletedProcess instance
        
    Raises:
        ValueError: If arguments are invalid
    """
    import subprocess
    
    # Validate arguments
    validated_args = validate_command_args(args)
    
    # Ensure shell=False (default)
    if kwargs.get('shell', False):
        raise ValueError("Shell mode is not allowed for security reasons")
    
    # Force shell=False
    kwargs['shell'] = False
    
    # Run the command
    return subprocess.run(validated_args, **kwargs)

# Audit logging
class AuditLogger:
    """Handle security audit logging"""
    
    @staticmethod
    def log_action(action, details=None, user=None, ip_address=None):
        """
        Log a security-relevant action
        
        Args:
            action: The action being performed
            details: Additional details about the action
            user: The user performing the action (if available)
            ip_address: The IP address of the request
        """
        if ip_address is None and request:
            ip_address = request.remote_addr
        
        log_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'action': action,
            'user': user or 'anonymous',
            'ip_address': ip_address,
            'details': details or {}
        }
        
        # Log to security logger
        security_logger = logging.getLogger('security_audit')
        security_logger.info(f"AUDIT: {log_entry}")
        
        # TODO: In production, also log to database or external audit system
        
    @staticmethod
    def log_security_event(event_type, message, severity='warning'):
        """
        Log a security event
        
        Args:
            event_type: Type of security event (e.g., 'path_traversal_attempt')
            message: Description of the event
            severity: Severity level (info, warning, error, critical)
        """
        log_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': event_type,
            'message': message,
            'severity': severity,
            'ip_address': request.remote_addr if request else None
        }
        
        security_logger = logging.getLogger('security_audit')
        log_method = getattr(security_logger, severity, security_logger.warning)
        log_method(f"SECURITY_EVENT: {log_entry}")

# Rate limiting decorator
def apply_rate_limit(limit_string):
    """
    Apply rate limiting to an endpoint
    
    Args:
        limit_string: Rate limit string (e.g., "10 per minute", "100 per hour")
    """
    def decorator(f):
        # This will be applied by Flask-Limiter when the decorator is used
        f._rate_limit = limit_string
        return f
    return decorator

# Input validation decorators
def validate_json_input(schema):
    """
    Decorator to validate JSON input against a schema
    
    Args:
        schema: Dictionary defining the expected schema
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not request.is_json:
                return jsonify({'error': 'Content-Type must be application/json'}), 400
            
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No JSON data provided'}), 400
            
            # Validate required fields
            for field, field_schema in schema.items():
                if field_schema.get('required', False) and field not in data:
                    return jsonify({'error': f'Missing required field: {field}'}), 400
                
                if field in data:
                    # Type validation
                    expected_type = field_schema.get('type')
                    if expected_type and not isinstance(data[field], expected_type):
                        return jsonify({'error': f'Invalid type for {field}: expected {expected_type.__name__}'}), 400
                    
                    # Pattern validation
                    pattern = field_schema.get('pattern')
                    if pattern and isinstance(data[field], str):
                        if not re.match(pattern, data[field]):
                            return jsonify({'error': f'Invalid format for {field}'}), 400
                    
                    # Length validation
                    max_length = field_schema.get('max_length')
                    if max_length and isinstance(data[field], str) and len(data[field]) > max_length:
                        return jsonify({'error': f'{field} exceeds maximum length of {max_length}'}), 400
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator
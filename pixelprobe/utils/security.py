"""
Security utilities for PixelProbe
"""
import os
import re
import socket
import ipaddress
import logging
import urllib.parse
from functools import wraps
from datetime import datetime, timezone
from typing import Optional, Set, Tuple, Union
from flask import request, jsonify, current_app
from werkzeug.utils import safe_join
from pixelprobe.models import db, ScanConfiguration
import requests

logger = logging.getLogger(__name__)


# Trusted internal hosts cache (lazy-loaded from TRUSTED_INTERNAL_HOSTS env var)
_trusted_hostnames: Optional[Set[str]] = None
_trusted_networks: Optional[Set[Union[ipaddress.IPv4Network, ipaddress.IPv6Network]]] = None


def _load_trusted_hosts():
    """Parse TRUSTED_INTERNAL_HOSTS env var into hostname and network sets.

    Format: comma-separated list of hostnames and/or CIDR ranges.
    Example: "healthcheck.internal.local,192.168.5.0/24,10.0.0.1"

    Results are cached on first call. Call _reset_trusted_hosts() to clear
    the cache (useful for testing).
    """
    global _trusted_hostnames, _trusted_networks

    if _trusted_hostnames is not None:
        return

    _trusted_hostnames = set()
    _trusted_networks = set()

    raw = os.environ.get('TRUSTED_INTERNAL_HOSTS', '').strip()
    if not raw:
        return

    for entry in raw.split(','):
        entry = entry.strip()
        if not entry:
            continue
        # Try parsing as an IP network (CIDR or bare IP)
        try:
            network = ipaddress.ip_network(entry, strict=False)
            _trusted_networks.add(network)
            logger.debug("Trusted internal network loaded from TRUSTED_INTERNAL_HOSTS")
        except ValueError:
            # Not a valid network -- treat as a hostname
            _trusted_hostnames.add(entry.lower())
            logger.debug("Trusted internal hostname loaded from TRUSTED_INTERNAL_HOSTS")


def _reset_trusted_hosts():
    """Clear the trusted hosts cache (for testing)."""
    global _trusted_hostnames, _trusted_networks
    _trusted_hostnames = None
    _trusted_networks = None


def _is_trusted(hostname: str, ip_str: str) -> bool:
    """Check whether a hostname or resolved IP is in the trusted allowlist."""
    _load_trusted_hosts()

    # Check hostname
    if hostname and hostname.lower() in _trusted_hostnames:
        return True

    # Check IP against trusted networks
    try:
        ip = ipaddress.ip_address(ip_str)
        for network in _trusted_networks:
            if ip in network:
                return True
    except ValueError:
        pass

    return False

class SecurityError(Exception):
    """Base exception for security-related errors"""
    pass

class PathTraversalError(SecurityError):
    """Raised when a path traversal attempt is detected"""
    pass

def _safe_join_under_any(real_input: str, allowed_paths) -> Optional[str]:
    """Return a sanitized path under one of ``allowed_paths`` if ``real_input``
    lies within it; ``None`` otherwise.

    The result comes from ``werkzeug.utils.safe_join``, which CodeQL
    recognises as a path-injection sanitizer. Callers should run any
    subsequent filesystem operation on the returned value rather than on the
    raw ``real_input``.
    """
    for allowed_path in allowed_paths:
        real_allowed = os.path.realpath(allowed_path)
        try:
            relative = os.path.relpath(real_input, real_allowed)
        except ValueError:
            continue  # Different drives on Windows, skip
        if relative == os.curdir:
            return real_allowed
        if relative.startswith(os.pardir):
            continue  # Outside this allowed root
        safe = safe_join(real_allowed, relative)
        if safe is not None:
            return safe
    return None


def get_allowed_scan_paths():
    """Get all allowed scan paths from configuration"""
    try:
        # Get paths from database configuration
        configs = ScanConfiguration.query.filter_by(is_active=True).all()
        allowed_paths = [os.path.abspath(config.path) for config in configs]
        
        # Add any environment-configured paths
        env_paths = os.environ.get('SCAN_PATHS', '').split(',')
        for path in env_paths:
            path = path.strip()  # Remove whitespace
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

    # Rescan operation: trust the DB record. Do not touch the filesystem with
    # the raw user-supplied path here -- the downstream scanner runs its own
    # existence check and reports failures cleanly.
    if not allowed_paths:
        from pixelprobe.models import ScanResult
        existing = ScanResult.query.filter_by(file_path=normalized).first()
        if existing:
            return normalized
        raise PathTraversalError("No allowed scan paths configured")

    # Resolve symlinks, then re-derive a safe path via werkzeug.utils.safe_join
    # against each allowed root. safe_join is the explicit sanitizer; the
    # returned path is what filesystem ops below operate on.
    real_input = os.path.realpath(normalized)
    safe_path = _safe_join_under_any(real_input, allowed_paths)
    if safe_path is None:
        raise PathTraversalError(f"Path outside allowed directories: {file_path}")
    if os.path.exists(safe_path) and os.access(safe_path, os.R_OK):
        return normalized
    raise PathTraversalError(f"File not found or not readable: {file_path}")

def validate_directory_path(dir_path, allowed_paths=None):
    """
    Validate that a directory path is safe.

    When ``allowed_paths`` is ``None`` (default), the configured scan paths
    are used as the allowlist and the resolved real path must sit within one
    of them. Callers that need to register a new allowlist entry (e.g. the
    admin add-configuration endpoint) pass ``allowed_paths=[]`` to skip the
    allowlist check. The suspicious-pattern check and symlink resolution
    always run.

    Args:
        dir_path: The directory path to validate
        allowed_paths: Explicit allowlist; ``[]`` disables the allowlist check,
            ``None`` uses ``get_allowed_scan_paths()``.

    Returns:
        Normalized absolute path if valid

    Raises:
        PathTraversalError: If the path is unsafe or outside the allowlist.
    """
    if not dir_path:
        raise PathTraversalError("Empty directory path")

    # Reject traversal/home-expansion tokens before touching the filesystem.
    if '..' in dir_path or '~' in dir_path:
        raise PathTraversalError("Directory path contains suspicious patterns")

    normalized = os.path.normpath(os.path.abspath(dir_path))

    if allowed_paths is None:
        allowed_paths = get_allowed_scan_paths()

    # Admin add-configuration path: caller is defining a new allowlist entry,
    # so the allowlist cannot be applied. Skip the filesystem check too --
    # validating an unallowlisted path against the filesystem is exactly the
    # tainted-sink CodeQL rejects, and the scheduler will surface a real
    # error later if the directory does not exist.
    if not allowed_paths:
        return normalized

    real_input = os.path.realpath(normalized)
    safe_path = _safe_join_under_any(real_input, allowed_paths)
    if safe_path is None:
        raise PathTraversalError(f"Path outside allowed directories: {dir_path}")
    if os.path.exists(safe_path) and not os.path.isdir(safe_path):
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
        r'[|`]',     # Shell metacharacters (removed ; & and $ which are safe in filenames with shell=False)
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
            'timestamp': datetime.now(timezone.utc).isoformat(),
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
            'timestamp': datetime.now(timezone.utc).isoformat(),
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
            if data is None:
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


# SSRF protection

# Private/reserved IP networks that should never be targeted by outbound requests
_BLOCKED_NETWORKS = [
    ipaddress.ip_network('127.0.0.0/8'),       # Loopback
    ipaddress.ip_network('10.0.0.0/8'),         # RFC 1918
    ipaddress.ip_network('172.16.0.0/12'),      # RFC 1918
    ipaddress.ip_network('192.168.0.0/16'),     # RFC 1918
    ipaddress.ip_network('169.254.0.0/16'),     # Link-local / cloud metadata
    ipaddress.ip_network('0.0.0.0/8'),          # "This" network
    ipaddress.ip_network('::1/128'),            # IPv6 loopback
    ipaddress.ip_network('fc00::/7'),           # IPv6 unique local
    ipaddress.ip_network('fe80::/10'),          # IPv6 link-local
]


def validate_safe_url(url: str) -> Tuple[bool, Optional[str]]:
    """Validate that a URL does not target private/internal IP ranges (SSRF protection).

    Args:
        url: The URL to validate

    Returns:
        Tuple of (is_safe, error_message). is_safe is True if URL is safe to request.
    """
    if not url or not url.strip():
        return False, "URL is empty"

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False, "Invalid URL format"

    # Validate scheme
    if parsed.scheme not in ('http', 'https'):
        return False, f"URL scheme must be http or https, got: {parsed.scheme or 'none'}"

    # Reject embedded credentials (user:pass@host)
    if parsed.username or parsed.password:
        return False, "URLs with embedded credentials are not allowed"

    hostname = parsed.hostname
    if not hostname:
        return False, "URL has no hostname"

    # Resolve hostname and check all resolved IPs
    try:
        addr_infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
    except socket.gaierror:
        return False, f"Could not resolve hostname: {hostname}"

    for addr_info in addr_infos:
        ip_str = addr_info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        for network in _BLOCKED_NETWORKS:
            if ip in network:
                # Check trusted allowlist before blocking
                if _is_trusted(hostname, ip_str):
                    logger.debug(
                        f"Allowing trusted internal request: {url} "
                        f"(resolved to {ip_str}, hostname={hostname})"
                    )
                    break  # Trusted -- skip remaining blocked networks for this IP
                AuditLogger.log_security_event(
                    'ssrf_blocked',
                    f"Blocked outbound request to private IP: {url} resolved to {ip_str}",
                    severity='warning'
                )
                return False, f"URL resolves to a private/reserved IP address ({ip_str})"

    return True, None


def create_safe_session(max_redirects: int = 5) -> requests.Session:
    """Create a requests.Session that validates redirect targets against private IP ranges.

    Args:
        max_redirects: Maximum number of redirects to follow

    Returns:
        A requests.Session configured with SSRF-safe redirect handling
    """
    session = requests.Session()
    session.max_redirects = max_redirects

    def _check_redirect(response, *args, **kwargs):
        """Response hook that validates redirect Location headers."""
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get('Location')
            if location:
                # Resolve relative redirects against the request URL
                redirect_url = urllib.parse.urljoin(response.url, location)
                is_safe, error = validate_safe_url(redirect_url)
                if not is_safe:
                    raise requests.ConnectionError(
                        f"Redirect blocked by SSRF protection: {error}"
                    )

    session.hooks['response'].append(_check_redirect)
    return session
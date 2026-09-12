"""
Security utilities for PixelProbe
"""
import os
import stat
import json
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
from pixelprobe.constants import SUPPORTED_EXTENSIONS
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

logger = logging.getLogger(__name__)

_AUDIT_SECRET_KEYS = frozenset({
    'authorization', 'cookie', 'password', 'token', 'token_digest', 'secret',
    'api_key', 'api_token', 'webhook_url', 'healthcheck_url',
})


def _redact_audit_details(value):
    """Remove credentials from structured audit details before logging."""
    if isinstance(value, str):
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme in {'http', 'https'} and parsed.hostname:
            return f'{parsed.scheme}://{parsed.hostname}/[redacted]'
        return value
    if isinstance(value, dict):
        return {
            str(key): '[redacted]' if str(key).lower() in _AUDIT_SECRET_KEYS
            else _redact_audit_details(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_audit_details(item) for item in value]
    return value


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

    # ~/$/% are legal in media filenames; containment is enforced below via
    # realpath + safe_join against the allowlist.
    suspicious_patterns = [
        r'\.\.',          # Parent directory references
        r'[\x00-\x1f]',   # Null byte and control characters
    ]

    for pattern in suspicious_patterns:
        if re.search(pattern, file_path):
            raise PathTraversalError(f"Suspicious pattern detected: {pattern}")
    
    # Get allowed paths if not provided
    if allowed_paths is None:
        allowed_paths = get_allowed_scan_paths()

    if not allowed_paths:
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


def resolve_authorized_media_file(file_path, allowed_paths=None, required_paths=None):
    """Resolve a readable supported regular media file under an active root.

    A database result is metadata, not permission to read a path. The returned
    canonical path must still be opened through ``open_authorized_media_file``
    when serving it so a replacement race cannot redirect the response.
    """
    if not isinstance(file_path, str) or not file_path:
        raise PathTraversalError("Invalid file path")
    roots = get_allowed_scan_paths() if allowed_paths is None else allowed_paths
    if not roots:
        raise PathTraversalError("No active scan roots configured")
    canonical = os.path.realpath(os.path.abspath(file_path))
    safe_path = _safe_join_under_any(canonical, roots)
    if safe_path is None or os.path.realpath(safe_path) != canonical:
        raise PathTraversalError("Path outside active scan roots")
    if required_paths is not None and _safe_join_under_any(canonical, required_paths) is None:
        raise PathTraversalError("Path outside this scan's authorized roots")
    if os.path.splitext(canonical)[1].lower() not in SUPPORTED_EXTENSIONS:
        raise PathTraversalError("Unsupported media file type")
    try:
        file_stat = os.stat(canonical, follow_symlinks=False)
    except OSError as exc:
        raise PathTraversalError("File not available") from exc
    if not stat.S_ISREG(file_stat.st_mode) or not os.access(canonical, os.R_OK):
        raise PathTraversalError("File is not a readable regular file")
    return canonical


def open_authorized_media_file(file_path, allowed_paths=None, required_paths=None):
    """Open an authorized file by descriptor and verify its stable identity."""
    canonical = resolve_authorized_media_file(file_path, allowed_paths, required_paths)
    roots = get_allowed_scan_paths() if allowed_paths is None else allowed_paths
    root = next(
        (os.path.realpath(candidate) for candidate in roots
         if _safe_join_under_any(canonical, [candidate]) is not None),
        None,
    )
    if root is None:
        raise PathTraversalError("Path outside active scan roots")
    relative = os.path.relpath(canonical, root)
    flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    directory_flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    directory_flags |= getattr(os, 'O_DIRECTORY', 0)
    if not hasattr(os, 'O_DIRECTORY') or not hasattr(os, 'O_NOFOLLOW'):
        raise PathTraversalError("Secure descriptor traversal is unavailable")
    try:
        directory_fd = os.open(os.path.sep, directory_flags)
        for component in filter(None, root.strip(os.path.sep).split(os.path.sep)):
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        parts = relative.split(os.sep)
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        fd = os.open(parts[-1], flags, dir_fd=directory_fd)
        os.close(directory_fd)
        opened = os.fstat(fd)
    except OSError as exc:
        try:
            os.close(fd)
        except (UnboundLocalError, OSError):
            pass
        try:
            os.close(directory_fd)
        except (UnboundLocalError, OSError):
            pass
        raise PathTraversalError("File changed or is unavailable") from exc
    if not stat.S_ISREG(opened.st_mode):
        os.close(fd)
        raise PathTraversalError("File changed while opening")
    return os.fdopen(fd, 'rb'), canonical, opened


def authorized_fd_path(fd):
    """Return the current-process pathname for an already-authorized fd.

    The descriptor remains the authority.  Callers that pass this path to a
    child must use ``safe_subprocess_run`` so the descriptor is inherited by
    that child rather than resolving the original mutable pathname again.
    """
    if not isinstance(fd, int) or fd < 0:
        raise ValueError("Invalid authorized file descriptor")
    if os.path.isdir('/proc/self/fd'):
        return f'/proc/self/fd/{fd}'
    if os.path.isdir('/dev/fd'):
        return f'/dev/fd/{fd}'
    raise PathTraversalError("Descriptor-backed media paths are unavailable")


def rewind_authorized_fd_path(path):
    """Rewind a descriptor-backed pathname before a fresh full-file reader.

    Linux procfd opens have independent offsets.  Darwin's /dev/fd duplicates
    the file description instead, so PIL or a child decoder advances the
    scan-owned descriptor unless it is rewound before the next reader.
    """
    match = re.fullmatch(r'/(?:proc/self|dev)/fd/(\d+)', str(path))
    if match:
        os.lseek(int(match.group(1)), 0, os.SEEK_SET)

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

    # An administrator may register a new root, but it must already resolve to
    # a real readable directory. No empty allowlist can become a bypass for a
    # nonexistent or special filesystem object.
    if not allowed_paths:
        real_path = os.path.realpath(normalized)
        if not os.path.isdir(real_path) or not os.access(real_path, os.R_OK):
            raise PathTraversalError("Directory is not readable")
        return real_path

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

def ensure_cli_safe_path(path):
    """Prefix relative paths with './' so a leading '-' is never parsed as a
    CLI option (convert has no '--' separator). Absolute paths pass through."""
    if not isinstance(path, str):
        path = str(path)
    if os.path.isabs(path):
        return path
    return os.path.join(os.curdir, path)

def validate_command_args(args):
    """Reject null bytes/newlines in argv. With shell=False this is NOT
    injection protection -- callers must use ensure_cli_safe_path() for paths."""
    if not isinstance(args, list):
        raise ValueError("Command arguments must be a list")

    dangerous_patterns = [
        r'\n|\r',  # Newlines
        r'\x00',   # Null bytes
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
    Safe wrapper for subprocess.run that validates arguments.

    Starts the child in its own session (POSIX) so that on a `timeout` the
    process is cleanly killed by subprocess.run without signalling the parent,
    and any stray children are isolated from PixelProbe's process group. This
    bounds ffmpeg/ffprobe calls that would otherwise hang a scan worker forever
    on a stalled mount.

    Args:
        args: Command arguments as a list
        **kwargs: Additional arguments for subprocess.run

    Returns:
        subprocess.CompletedProcess instance

    Raises:
        ValueError: If arguments are invalid
        subprocess.TimeoutExpired: If the command exceeds `timeout` seconds
    """
    import os
    import subprocess

    # Validate arguments
    validated_args = validate_command_args(args)

    # Ensure shell=False (default)
    if kwargs.get('shell', False):
        raise ValueError("Shell mode is not allowed for security reasons")

    # Force shell=False
    kwargs['shell'] = False

    # A /proc/self/fd or /dev/fd input is only meaningful in a child when its
    # source descriptor survives exec.  Add those descriptors automatically so
    # every existing ffmpeg, ffprobe and ImageMagick call remains descriptor
    # backed without each call site having to duplicate this contract.
    descriptor_fds = set(kwargs.pop('pass_fds', ()) or ())
    if os.name == 'posix':
        for arg in validated_args:
            match = re.fullmatch(r'/(?:proc/self|dev)/fd/(\d+)', arg)
            if match:
                descriptor_fds.add(int(match.group(1)))
        if descriptor_fds:
            for fd in descriptor_fds:
                os.lseek(fd, 0, os.SEEK_SET)
            kwargs['pass_fds'] = tuple(sorted(descriptor_fds))
    elif descriptor_fds:
        raise ValueError("Descriptor inheritance is unavailable on this platform")

    # Isolate the child in its own session so a timeout-kill targets it (and not
    # the parent), and orphaned helpers don't share PixelProbe's process group.
    if os.name == 'posix':
        kwargs.setdefault('start_new_session', True)

    # Run the command
    return subprocess.run(validated_args, **kwargs)

# Audit logging
class AuditLogger:
    """Handle security audit logging"""
    
    @staticmethod
    def log_action(action, details=None, user=None, ip_address=None, target=None, outcome='success'):
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
        safe_details = _redact_audit_details(details or {})
        actor_id = None
        if user is None:
            try:
                from flask_login import current_user
                request_user = getattr(request, 'current_user', None)
                principal = request_user if getattr(request_user, 'is_authenticated', False) else current_user
                if principal.is_authenticated:
                    user = principal.username
                    actor_id = principal.id
                else:
                    user = 'anonymous'
            except Exception:
                user = 'anonymous'
        elif hasattr(user, 'id'):
            actor_id = user.id
            user = getattr(user, 'username', str(actor_id))
        log_entry = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'action': action,
            'user': user,
            'ip_address': ip_address,
            'details': safe_details
        }
        
        # Log to security logger
        security_logger = logging.getLogger('security_audit')
        security_logger.info("AUDIT: %s", log_entry)
        AuditLogger._persist(actor_id, action, target, outcome, safe_details, ip_address)

    @staticmethod
    def _persist(actor_id, action, target, outcome, details, ip_address):
        """Persist independently so routine LogEntry retention cannot erase it."""
        try:
            from sqlalchemy import text
            with db.engine.begin() as conn:
                details_value = json.dumps(details)
                details_sql = 'CAST(:details AS json)' if conn.dialect.name == 'postgresql' else ':details'
                conn.execute(text("""
                    INSERT INTO security_audit_events
                    (actor_id, action, target, outcome, details, ip_address, created_at)
                    VALUES (:actor_id, :action, :target, :outcome, """ + details_sql + ", :ip_address, :created_at)"), {
                    'actor_id': actor_id,
                    'action': action,
                    'target': target,
                    'outcome': outcome,
                    'details': details_value,
                    'ip_address': ip_address,
                    'created_at': datetime.now(timezone.utc),
                })
        except Exception:
            logging.getLogger('security_audit').exception('Failed to persist security audit event')
        
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
            'message': _redact_audit_details({'message': message})['message'],
            'severity': severity,
            'ip_address': request.remote_addr if request else None
        }
        
        security_logger = logging.getLogger('security_audit')
        log_method = getattr(security_logger, severity, security_logger.warning)
        log_method(f"SECURITY_EVENT: {log_entry}")
        AuditLogger.log_action(event_type, {'message': message}, outcome=severity)

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


# Cloud metadata endpoints, blocked for every outbound target regardless of the
# looser policy applied to operator-configured service hosts below.
_CLOUD_METADATA_IPS = frozenset({
    '169.254.169.254',    # AWS / Azure / GCP / DigitalOcean / OpenStack
    '169.254.170.2',      # AWS ECS task metadata
    'fd00:ec2::254',      # AWS IMDSv2 over IPv6
    '100.100.100.200',    # Alibaba Cloud
})


def _canonical_ip(ip_str):
    ip = ipaddress.ip_address(ip_str)
    return getattr(ip, 'ipv4_mapped', None) or ip


def _is_blocked_outbound_ip(ip_str):
    ip = _canonical_ip(ip_str)
    return str(ip) in _CLOUD_METADATA_IPS or any(ip in network for network in _BLOCKED_NETWORKS)


def _safe_resolved_address(host, port):
    """Resolve and select a permitted literal address for the actual socket."""
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OSError('Outbound host could not be resolved') from exc
    for address in addresses:
        literal = address[4][0]
        try:
            _canonical_ip(literal)
        except ValueError:
            continue
        if _is_blocked_outbound_ip(literal):
            if str(_canonical_ip(literal)) in _CLOUD_METADATA_IPS:
                continue
            if not _is_trusted(host, literal):
                continue
        return literal
    raise OSError('Outbound host resolved only to blocked addresses')


class _BoundHTTPConnection(HTTPConnection):
    def _new_conn(self):
        hostname = self._dns_host
        self._dns_host = _safe_resolved_address(hostname, self.port)
        try:
            return super()._new_conn()
        finally:
            self._dns_host = hostname


class _BoundHTTPSConnection(HTTPSConnection):
    def _new_conn(self):
        hostname = self._dns_host
        self._dns_host = _safe_resolved_address(hostname, self.port)
        try:
            return super()._new_conn()
        finally:
            self._dns_host = hostname


class _BoundHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _BoundHTTPConnection


class _BoundHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _BoundHTTPSConnection


class _BoundAddressAdapter(HTTPAdapter):
    """Connect to the vetted IP literal while retaining the URL host for TLS."""
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            'http': _BoundHTTPConnectionPool,
            'https': _BoundHTTPSConnectionPool,
        }


def validate_outbound_host(host: str, port: int = 0) -> Tuple[bool, Optional[str]]:
    """Validate a bare operator-configured service hostname (no URL scheme).

    Looser than validate_safe_url on purpose: private, LAN and loopback targets
    stay allowed because a self-hosted SMTP relay legitimately lives on
    localhost, a Docker network, or the LAN, and requiring a trusted-hosts
    allowlist entry before mail works would block the common setup. Cloud
    metadata and link-local targets are still refused, so the loosening cannot
    be turned into a credential-theft path.

    Unresolvable hosts pass: the connection then fails at connect time without
    ever reaching an internal address. Callers should re-validate immediately
    before connecting (not only when the config is saved) so a DNS rebind cannot
    repoint a stored hostname at a blocked target.

    Args:
        host: Hostname or IP, without scheme (brackets around IPv6 are stripped)
        port: Optional port, used only to narrow address resolution

    Returns:
        Tuple of (is_safe, error_message).
    """
    if not host or not host.strip():
        return False, "Host is empty"

    host = host.strip().strip('[]')

    try:
        if str(_canonical_ip(host)) in _CLOUD_METADATA_IPS:
            return False, f"Host is a blocked cloud metadata address ({host})"
    except ValueError:
        pass

    try:
        addr_infos = socket.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # Not resolvable here; connecting will fail without reaching any host.
        return True, None

    for addr_info in addr_infos:
        ip_str = addr_info[4][0]
        if _is_blocked_outbound_ip(ip_str) and str(_canonical_ip(ip_str)) in _CLOUD_METADATA_IPS:
            AuditLogger.log_security_event(
                'ssrf_blocked',
                f"Blocked outbound connection to cloud metadata IP: {host} resolved to {ip_str}",
                severity='warning'
            )
            return False, f"Host resolves to a blocked cloud metadata address ({ip_str})"
        try:
            ip = _canonical_ip(ip_str)
        except ValueError:
            continue
        if ip.is_link_local:
            AuditLogger.log_security_event(
                'ssrf_blocked',
                f"Blocked outbound connection to link-local IP: {host} resolved to {ip_str}",
                severity='warning'
            )
            return False, f"Host resolves to a link-local address ({ip_str})"

    return True, None


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
            ip = _canonical_ip(ip_str)
        except ValueError:
            continue

        if str(ip) in _CLOUD_METADATA_IPS:
            AuditLogger.log_security_event(
                'ssrf_blocked',
                f"Blocked outbound request to cloud metadata IP: {hostname} resolved to {ip}",
                severity='warning'
            )
            return False, f"URL resolves to a private/reserved IP address ({ip})"

        for network in _BLOCKED_NETWORKS:
            if ip in network:
                # Only the hostname and resolved IP are recorded, never the full
                # URL: a webhook URL is itself a credential (Slack and Discord
                # endpoints are bearer secrets in the path), and ntfy URLs carry
                # the topic. That is enough to diagnose a blocked target.
                if _is_trusted(hostname, ip_str):
                    # Resolved IP only. The hostname comes from caller-supplied
                    # provider config, so keeping it out of the application log
                    # avoids writing attacker-influenced text there; the security
                    # audit trail below is where target identity belongs.
                    logger.debug(f"Allowing trusted internal request (resolved to {ip_str})")
                    break  # Trusted -- skip remaining blocked networks for this IP
                AuditLogger.log_security_event(
                    'ssrf_blocked',
                    f"Blocked outbound request to private IP: {hostname} resolved to {ip_str}",
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
    session.trust_env = False
    session.max_redirects = max_redirects

    # Retry transient failures (connection errors, 429, 5xx) with backoff so a
    # momentary blip does not drop an idempotent GET healthcheck ping. POST is
    # deliberately NOT retried: a notification/webhook POST that the provider
    # already delivered before returning 5xx must not be re-sent (duplicate
    # alerts), so only idempotent methods are retried.
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(['GET']),
        raise_on_status=False,
    )
    adapter = _BoundAddressAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)

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

"""
Timezone utilities for consistent UTC storage and configured timezone display
"""
import os
import pytz
from datetime import datetime, timezone
from typing import Optional, Union
import logging

logger = logging.getLogger(__name__)

def get_configured_timezone() -> pytz.timezone:
    """
    Get the timezone configured via TZ environment variable.
    Falls back to UTC if not configured or invalid.
    """
    tz_name = os.environ.get('TZ', 'UTC')
    try:
        return pytz.timezone(tz_name)
    except pytz.exceptions.UnknownTimeZoneError:
        logger.warning(f"Unknown timezone '{tz_name}', falling back to UTC")
        return pytz.UTC

def get_configured_timezone_name() -> str:
    """
    Get the configured timezone name as a string.
    Returns the TZ environment variable value or 'UTC' if not set.
    """
    return os.environ.get('TZ', 'UTC')

def utc_now() -> datetime:
    """
    Get current time in UTC with timezone awareness.
    Always use this instead of datetime.now() or datetime.utcnow()
    """
    return datetime.now(timezone.utc)

def to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Convert any datetime to UTC with timezone awareness.
    If already UTC, returns as-is. If naive, assumes it's in configured timezone.
    """
    if dt is None:
        return None
    
    if dt.tzinfo is None:
        # Naive datetime - assume it's in configured timezone
        configured_tz = get_configured_timezone()
        localized = configured_tz.localize(dt)
        return localized.astimezone(pytz.UTC)
    elif dt.tzinfo != pytz.UTC:
        # Has timezone but not UTC - convert
        return dt.astimezone(pytz.UTC)
    else:
        # Already UTC
        return dt

def from_utc_to_configured(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Convert UTC datetime to configured timezone for display.
    This should be used when sending dates to the API/UI.
    """
    if dt is None:
        return None
    
    configured_tz = get_configured_timezone()
    
    if dt.tzinfo is None:
        # Naive datetime - assume it's UTC
        dt = pytz.UTC.localize(dt)
    elif dt.tzinfo != pytz.UTC:
        # Has timezone but not UTC - convert to UTC first
        dt = dt.astimezone(pytz.UTC)
    
    # Convert to configured timezone
    return dt.astimezone(configured_tz)

def format_datetime_for_display(dt: Optional[datetime], 
                               include_timezone: bool = False,
                               format_string: Optional[str] = None) -> str:
    """
    Format datetime for display in configured timezone.
    
    Args:
        dt: DateTime to format (assumed to be UTC if stored in DB)
        include_timezone: Whether to include timezone abbreviation
        format_string: Custom strftime format string
    
    Returns:
        Formatted string or 'N/A' if dt is None
    """
    if dt is None:
        return 'N/A'
    
    # Convert to configured timezone
    display_dt = from_utc_to_configured(dt)
    
    if format_string:
        result = display_dt.strftime(format_string)
    else:
        # Default format: YYYY-MM-DD HH:MM:SS
        result = display_dt.strftime('%Y-%m-%d %H:%M:%S')
    
    if include_timezone:
        result += f" {display_dt.strftime('%Z')}"
    
    return result

def parse_datetime_from_string(dt_string: str, 
                              assume_configured_tz: bool = True) -> Optional[datetime]:
    """
    Parse datetime string and return UTC datetime.
    
    Args:
        dt_string: DateTime string to parse
        assume_configured_tz: If True and no timezone in string, assume configured timezone
    
    Returns:
        UTC datetime with timezone awareness
    """
    if not dt_string:
        return None
    
    try:
        # Try parsing with timezone
        if 'T' in dt_string and ('+' in dt_string or 'Z' in dt_string):
            # ISO format with timezone
            if dt_string.endswith('Z'):
                dt = datetime.fromisoformat(dt_string.replace('Z', '+00:00'))
            else:
                dt = datetime.fromisoformat(dt_string)
            return dt.astimezone(pytz.UTC)
        else:
            # No timezone in string
            if 'T' in dt_string:
                # ISO format without timezone
                dt = datetime.fromisoformat(dt_string)
            else:
                # Try common formats
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%m/%d/%Y %H:%M:%S']:
                    try:
                        dt = datetime.strptime(dt_string, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    raise ValueError(f"Could not parse datetime: {dt_string}")
            
            # Naive datetime - handle based on assumption
            if assume_configured_tz:
                configured_tz = get_configured_timezone()
                localized = configured_tz.localize(dt)
                return localized.astimezone(pytz.UTC)
            else:
                # Assume UTC
                return pytz.UTC.localize(dt)
    
    except Exception as e:
        logger.error(f"Error parsing datetime '{dt_string}': {e}")
        return None
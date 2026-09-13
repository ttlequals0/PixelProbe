"""Rate limiting configuration for the application"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps
import logging

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address, default_limits=[])


def rate_limit(limit_string):
    """Decorator to apply rate limits using the app's limiter.

    Args:
        limit_string: Rate limit string (e.g., "10 per minute", "100 per hour")
    """
    return limiter.limit(limit_string)


def exempt_from_rate_limit(f):
    """Decorator to exempt a function from rate limiting"""
    return limiter.exempt(f)

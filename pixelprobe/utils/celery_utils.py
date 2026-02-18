"""Celery utility functions for PixelProbe"""

import logging
from flask import current_app

logger = logging.getLogger(__name__)


def check_celery_available():
    """Check if Celery is available and broker is reachable.

    Returns True if Celery is configured, has a broker URL, and the broker
    responds to a ping. Returns False otherwise, allowing callers to
    fall back to direct (non-Celery) execution.
    """
    celery_enabled = current_app.config.get('CELERY_BROKER_URL') and hasattr(current_app, 'celery')

    if celery_enabled:
        try:
            current_app.celery.control.ping(timeout=1.0)
        except Exception as e:
            logger.warning(f"Celery broker connection failed: {e}. Falling back to direct execution.")
            celery_enabled = False

    return celery_enabled

"""
Context variables for tagging log entries with scan/task identifiers.

Usage:
    from pixelprobe.utils.log_context import scan_log_context

    with scan_log_context(scan_id, celery_task_id):
        logger.info("This log will be tagged with scan_id and celery_task_id")
"""

from contextvars import ContextVar
from contextlib import contextmanager
from typing import Optional

current_scan_id: ContextVar[Optional[str]] = ContextVar('current_scan_id', default=None)
current_celery_task_id: ContextVar[Optional[str]] = ContextVar('current_celery_task_id', default=None)


@contextmanager
def scan_log_context(scan_id, celery_task_id=None):
    """Context manager that sets scan_id and celery_task_id for log tagging.

    Logs emitted inside this context will have these values attached
    by the DatabaseLogHandler.
    """
    scan_token = current_scan_id.set(scan_id)
    task_token = current_celery_task_id.set(celery_task_id)
    try:
        yield
    finally:
        current_scan_id.reset(scan_token)
        current_celery_task_id.reset(task_token)

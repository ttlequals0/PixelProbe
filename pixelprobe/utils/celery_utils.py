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


def disable_dispatch_result_subscription(celery_instance):
    """Stop apply_async subscribing the producer to result pub/sub channels.

    send_task calls backend.on_task_call -> SUBSCRIBE per dispatched task.
    Producers read stored result meta (safe_task_get) and never drain that
    socket, so its replies pile up until Redis force-closes the connection
    (client-output-buffer-limit, observed every ~3min during cleanup runs).
    AsyncResult.get() still works: it subscribes itself (add_pending_result)
    and reconciles from stored meta. Producer-side GroupResult.join_native()
    would NOT resolve promptly without the dispatch-time subscription - the
    only group joins live worker-side (discovery), where this hook was
    already skipped via task_join_will_block().
    """
    celery_instance.backend.on_task_call = lambda producer, task_id: None


def is_db_connection_corruption(exc) -> bool:
    """Detect post-fork PostgreSQL connection corruption.

    Surfaces as "PGRES_TUPLES_OK and no message from the libpq" when a forked
    worker inherits and uses a parent's libpq socket. The connection is dead;
    retrying the same task on the same connection will not help.
    """
    msg = str(exc)
    return "PGRES_TUPLES_OK" in msg or "no message from the libpq" in msg

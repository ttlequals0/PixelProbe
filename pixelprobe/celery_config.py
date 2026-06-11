"""
Celery Configuration Module for PixelProbe
"""

import atexit
import logging
import os

from celery import Celery
from celery.signals import worker_process_init

from pixelprobe.utils.celery_utils import disable_dispatch_result_subscription


def _make_context_task(celery_instance, flask_app):
    """Create a ContextTask class that runs Celery tasks inside a Flask app context."""
    class ContextTask(celery_instance.Task):
        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return self.run(*args, **kwargs)
    celery_instance.Task = ContextTask


def create_celery(app=None):
    """
    Create and configure Celery instance for PixelProbe
    Follows the audit plan specifications for task queue implementation
    """
    
    # Get broker and backend URLs from environment or app config
    if app:
        broker_url = app.config.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
        result_backend = app.config.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
        app_name = app.import_name
    else:
        broker_url = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
        result_backend = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
        app_name = 'pixelprobe'
    
    # Create Celery instance
    celery = Celery(
        app_name,
        broker=broker_url,
        backend=result_backend,
        include=['pixelprobe.tasks', 'pixelprobe.tasks_parallel']  # Register all task modules with the worker
    )
    
    # Configure Celery settings
    celery.conf.update({
        # Task execution settings
        'task_serializer': 'json',
        'accept_content': ['json'],
        'result_serializer': 'json',
        'timezone': 'UTC',
        'enable_utc': True,
        
        # Task routing and reliability
        'task_routes': {
            'pixelprobe.tasks.*': {'queue': 'pixelprobe'},
        },
        'task_default_queue': 'pixelprobe',
        'task_default_exchange': 'pixelprobe',
        'task_default_exchange_type': 'direct',
        'task_default_routing_key': 'pixelprobe',
        
        # Retry and timeout settings
        'task_acks_late': True,  # Acknowledge only after successful completion to prevent task loss
        'task_reject_on_worker_lost': True,
        # No global timeouts - scan tasks with 1M+ files can take 3-4+ hours
        # Individual media validation has its own timeouts (video: dynamic based on size)
        'task_soft_time_limit': None,  # No soft limit - scans need unlimited time
        'task_time_limit': None,        # No hard limit - scans need unlimited time
        
        # Worker settings
        'worker_prefetch_multiplier': 1,  # One task per worker at a time
        # max-tasks-per-child is set by the CLI flag in celery_worker.py (CLI wins)

        # Task deduplication to prevent multiple workers from picking up same retry
        'task_track_started': True,  # Track when tasks actually start execution
        'task_ignore_result': False,  # Store results for monitoring

        # Monitoring task events are DISABLED. Under a high-throughput scan (e.g.
        # 1M+ tiny immich thumbnails hashing in sub-millisecond bursts), the
        # event stream floods the kombu event-loop hub and trips an
        # "Unrecoverable error: AssertionError()" in hub.fire_timers that wedges
        # the worker. PixelProbe's UI reads progress from the DB (/api/*-status),
        # not Celery events, so disabling these costs nothing here.
        'worker_send_task_events': False,
        'task_send_sent_event': False,
        
        # Result backend settings
        'result_expires': 86400,  # Results expire after 24 hours
        'result_persistent': True,
        
        # Redis visibility timeout for long-running tasks
        # v2.5.54: Added resilience settings for connection stability
        'broker_transport_options': {
            'visibility_timeout': 86400,  # 24 hours for large scans
            'fanout_prefix': True,
            'fanout_patterns': True,
            'priority_steps': [0, 3, 6, 9],  # Enable priority support (0=highest, 9=lowest)
            'socket_timeout': 30,
            'socket_keepalive': True,
            'socket_connect_timeout': 30,
            'retry_on_timeout': True,
            'health_check_interval': 60,
        },

        # v2.5.54: Result backend transport options for connection stability
        'result_backend_transport_options': {
            'socket_timeout': 30,
            'socket_keepalive': True,
            'socket_connect_timeout': 30,
            'retry_on_timeout': True,
            'health_check_interval': 60,
        },

        # Task priority settings
        # Priority levels: 0-2 (urgent), 3-5 (normal/high), 6-8 (background), 9 (lowest)
        # Scan tasks use priority 3 (high)
        # Maintenance tasks (file changes, cleanup) use priority 7 (background)
        'task_default_priority': 5,  # Default for tasks without explicit priority
        
        # Connection retry settings (Celery 6.0 compatibility)
        'broker_connection_retry_on_startup': True,  # Fix deprecation warning

        # NOTE: data-retention-cleanup is scheduled by the single-leader
        # MediaScheduler (APScheduler) daily at 04:00, NOT Celery beat. No beat
        # process is launched, so a beat_schedule entry here would never fire.
    })

    # Flask app context integration
    if app:
        _make_context_task(celery, app)
        celery.conf.update(app.config)

    disable_dispatch_result_subscription(celery)

    return celery


# Create standalone Celery instance for worker processes with Flask app context
# Workers need Flask app context to access db object
def _create_worker_celery():
    """Create Celery instance for workers with Flask app context support"""
    celery = create_celery()

    # Import Flask app and set up context task for workers
    from app import app
    _make_context_task(celery, app)
    return celery

celery_app = _create_worker_celery()


def init_celery(app, celery):
    """Initialize Celery with Flask app"""
    celery.conf.update(app.config)
    _make_context_task(celery, app)
    return celery


@worker_process_init.connect
def _setup_worker_process(**kwargs):
    """Initialize each forked Celery worker child process.

    1. Dispose the inherited SQLAlchemy engine so each child builds a fresh
       connection pool. Without this, child processes share libpq sockets
       with the parent, which surfaces as "PGRES_TUPLES_OK and no message
       from the libpq" - a NotImplementedError when concurrent SQLAlchemy
       queries try to read a row whose cursor was torn out from under them.
    2. Attach a fresh DatabaseLogHandler. The handler set up in the parent
       process has a dead _writer_thread because threads don't survive
       fork(); this signal fires once per child and replaces it.

    Both steps log entry/exit so a fork-time failure is visible in the
    worker container logs instead of silently leaving the child unable to
    process tasks (regression seen post v2.6.41).
    """
    pid = os.getpid()
    init_logger = logging.getLogger(__name__)
    init_logger.info("_setup_worker_process: starting in worker pid=%s", pid)

    from app import app
    from pixelprobe.models import db
    from pixelprobe.utils.log_handler import DatabaseLogHandler

    try:
        with app.app_context():
            db.engine.dispose()
        init_logger.info(
            "_setup_worker_process: db.engine.dispose() ok in pid=%s", pid
        )
    except Exception as e:
        init_logger.exception(
            "_setup_worker_process: db.engine.dispose() failed in pid=%s: %s",
            pid, e,
        )
        # Continue: a fresh connection on first session use is the fallback.

    handler = DatabaseLogHandler(app)
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    atexit.register(handler.shutdown)
    init_logger.info("_setup_worker_process: complete in worker pid=%s", pid)

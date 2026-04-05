"""
Celery Configuration Module for PixelProbe
"""

import atexit
import logging
import os

from celery import Celery
from celery.signals import worker_process_init


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
        include=['pixelprobe.tasks']  # Auto-discover tasks
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
        'worker_max_tasks_per_child': 50,  # Restart worker after 50 tasks
        'worker_max_memory_per_child': 2048000,  # Restart worker if RSS exceeds 2GB (in KB)

        # Task deduplication to prevent multiple workers from picking up same retry
        'task_track_started': True,  # Track when tasks actually start execution
        'task_ignore_result': False,  # Store results for monitoring
        
        # Monitoring
        'worker_send_task_events': True,
        'task_send_sent_event': True,
        
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

        # Celery Beat scheduled tasks (P2 Data Retention Implementation)
        'beat_schedule': {
            'data-retention-cleanup': {
                'task': 'pixelprobe.tasks.run_retention_cleanup',
                'schedule': 86400.0,  # Run daily (24 * 60 * 60 seconds)
                'options': {'priority': 9}  # Lowest priority - maintenance task
            },
        },
    })

    # Flask app context integration
    if app:
        _make_context_task(celery, app)
        celery.conf.update(app.config)
    
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
def _setup_db_log_handler_in_worker(**kwargs):
    """Attach DatabaseLogHandler in each forked Celery worker child process.

    Threads don't survive fork(), so the handler set up in the parent process
    has a dead _writer_thread in children.  This signal fires once per child
    and creates a fresh handler with its own background writer thread.
    """
    from app import app
    from pixelprobe.utils.log_handler import DatabaseLogHandler

    handler = DatabaseLogHandler(app)
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    atexit.register(handler.shutdown)

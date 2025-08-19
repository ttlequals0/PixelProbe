"""
Celery Configuration Module for PixelProbe
P1 Implementation per 2.1_AUDIT_IMPLEMENTATION_PLAN.md
"""

from celery import Celery
import os


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
        'task_acks_late': True,
        'task_reject_on_worker_lost': True,
        'task_soft_time_limit': 300,  # 5 minutes soft limit
        'task_time_limit': 600,       # 10 minutes hard limit
        
        # Worker settings
        'worker_prefetch_multiplier': 1,  # One task per worker at a time
        'worker_max_tasks_per_child': 50,  # Restart worker after 50 tasks
        
        # Monitoring
        'worker_send_task_events': True,
        'task_send_sent_event': True,
        
        # Result backend settings
        'result_expires': 3600,  # Results expire after 1 hour
        'result_persistent': True,
    })
    
    # Flask app context integration
    if app:
        class ContextTask(celery.Task):
            """Make celery tasks work with Flask app context"""
            def __call__(self, *args, **kwargs):
                with app.app_context():
                    return self.run(*args, **kwargs)
        
        celery.Task = ContextTask
        
        # Update configuration from Flask app
        celery.conf.update(app.config)
    
    return celery


# Create standalone Celery instance for worker processes
celery_app = create_celery()


def init_celery(app, celery):
    """Initialize Celery with Flask app"""
    celery.conf.update(app.config)
    
    class ContextTask(celery.Task):
        """Make celery tasks work with Flask app context"""
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)
    
    celery.Task = ContextTask
    return celery
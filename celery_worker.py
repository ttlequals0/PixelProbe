#!/usr/bin/env python3
"""
Celery Worker Script for PixelProbe

This script starts a Celery worker for processing PixelProbe tasks.
It can be run standalone or as part of the Docker deployment.

Usage:
    python celery_worker.py

Environment Variables:
    CELERY_BROKER_URL - Redis broker URL (default: redis://localhost:6379/0)
    CELERY_RESULT_BACKEND - Redis result backend URL (default: redis://localhost:6379/0)
    CELERY_LOG_LEVEL - Log level (default: INFO)
    CELERY_CONCURRENCY - Number of worker processes (default: 4)
"""

import os
import sys
import logging
from celery import Celery

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import Celery from the main app which has Flask context
# CRITICAL: Must import from app.py to get the Flask-initialized Celery instance
from app import celery as celery_app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Start the Celery worker"""
    logger.info("Starting PixelProbe Celery worker...")
    
    # Get configuration from environment
    broker_url = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
    result_backend = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
    log_level = os.getenv('CELERY_LOG_LEVEL', 'INFO')
    concurrency = int(os.getenv('CELERY_CONCURRENCY', '4'))
    
    logger.info(f"Broker: {broker_url}")
    logger.info(f"Result Backend: {result_backend}")
    logger.info(f"Log Level: {log_level}")
    logger.info(f"Concurrency: {concurrency}")
    
    # max-tasks-per-child is env-overridable. Default raised from 50 to 1000: an
    # integrity scan over 1M+ tiny thumbnails completes 50 sub-ms hashes in well
    # under a second, so a low value caused a fork storm (tens of thousands of
    # child respawns) that hammered the prefork pool and event loop. Memory is
    # still bounded by --max-memory-per-child for heavy/large-file media tasks.
    try:
        max_tasks_per_child = int(os.getenv('CELERY_MAX_TASKS_PER_CHILD', '1000'))
    except (TypeError, ValueError):
        max_tasks_per_child = 1000

    # Start the worker.
    # --without-gossip/--without-mingle/--without-heartbeat remove worker-to-worker
    # event-loop timers (unused with a single worker on a Redis broker) which,
    # together with task events, trip the kombu hub.fire_timers AssertionError
    # under high-throughput scans.
    try:
        rc = celery_app.worker_main([
            'worker',
            '--loglevel', log_level.lower(),
            '--concurrency', str(concurrency),
            '--queues', 'pixelprobe',
            '--hostname', f'pixelprobe-worker@%h',
            '--max-tasks-per-child', str(max_tasks_per_child),
            '--max-memory-per-child', '2000000',  # 2GB limit per child for media processing
            '--without-gossip',
            '--without-mingle',
            '--without-heartbeat',
        ])
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
        return
    except Exception as e:
        logger.error(f"Worker failed: {e}")
        sys.exit(1)

    # Propagate Celery's own exit code: 0 for a clean (warm/SIGTERM) shutdown,
    # non-zero for an unrecoverable error. Exiting non-zero lets the container's
    # restart policy bring a crashed worker back, while a clean shutdown exits 0
    # (no false restart / misleading error on a normal stop).
    if rc:
        logger.error(f"Celery worker exited with code {rc}; exiting non-zero to trigger container restart")
    sys.exit(rc or 0)


if __name__ == '__main__':
    main()
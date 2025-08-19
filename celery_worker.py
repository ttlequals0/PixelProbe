#!/usr/bin/env python3
"""
Celery Worker Script for PixelProbe
P1 Implementation per 2.1_AUDIT_IMPLEMENTATION_PLAN.md

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
    
    # Start the worker
    try:
        celery_app.worker_main([
            'worker',
            '--loglevel', log_level.lower(),
            '--concurrency', str(concurrency),
            '--queues', 'pixelprobe',
            '--hostname', f'pixelprobe-worker@%h',
            '--max-tasks-per-child', '50',
            '--max-memory-per-child', '500000',  # 500MB limit per child
            '--time-limit', '600',  # 10 minutes hard limit
            '--soft-time-limit', '300',  # 5 minutes soft limit
            '--without-gossip',
            '--without-mingle',
            '--without-heartbeat'
        ])
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.error(f"Worker failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
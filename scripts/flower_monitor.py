#!/usr/bin/env python3
"""
Flower Monitoring Dashboard for PixelProbe Celery Tasks
P1 Implementation per 2.1_AUDIT_IMPLEMENTATION_PLAN.md

This script starts the Flower web interface for monitoring Celery tasks.

Usage:
    python flower_monitor.py

Environment Variables:
    CELERY_BROKER_URL - Redis broker URL (default: redis://localhost:6379/0)
    FLOWER_PORT - Port for Flower dashboard (default: 5555)
    FLOWER_URL_PREFIX - URL prefix for reverse proxy (optional)
"""

import os
import sys
import logging

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import Celery configuration
from celery_config import celery_app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Start the Flower monitoring dashboard"""
    logger.info("Starting PixelProbe Flower monitoring dashboard...")
    
    # Get configuration from environment
    broker_url = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
    port = int(os.getenv('FLOWER_PORT', '5555'))
    url_prefix = os.getenv('FLOWER_URL_PREFIX', '')
    
    logger.info(f"Broker: {broker_url}")
    logger.info(f"Port: {port}")
    if url_prefix:
        logger.info(f"URL Prefix: {url_prefix}")
    
    logger.info(f"Dashboard will be available at: http://localhost:{port}")
    if url_prefix:
        logger.info(f"With prefix: http://localhost:{port}{url_prefix}")
    
    # Build Flower command arguments
    flower_args = [
        'flower',
        '--broker', broker_url,
        '--port', str(port),
        '--max_tasks', '10000',
        '--tasks_columns', 'name,uuid,state,args,kwargs,result,received,started,runtime,worker'
    ]
    
    if url_prefix:
        flower_args.extend(['--url_prefix', url_prefix])
    
    # Start Flower
    try:
        celery_app.start(flower_args)
    except KeyboardInterrupt:
        logger.info("Flower dashboard stopped by user")
    except Exception as e:
        logger.error(f"Flower dashboard failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
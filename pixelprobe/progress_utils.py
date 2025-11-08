"""
Progress tracking utilities for PixelProbe
Shared between scan_service and tasks to avoid circular imports
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def get_redis_client():
    """Get Redis client from environment configuration"""
    import os
    import redis

    broker_url = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
    # Parse redis://host:port/db format
    if broker_url.startswith('redis://'):
        url = broker_url.replace('redis://', '')
        parts = url.split('/')
        host_port = parts[0].split(':')
        host = host_port[0]
        port = int(host_port[1]) if len(host_port) > 1 else 6379
        db_num = int(parts[1]) if len(parts) > 1 else 0

        try:
            return redis.Redis(host=host, port=port, db=db_num,
                             socket_timeout=5, socket_connect_timeout=5)
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            return None
    return None


def update_scan_progress_redis(scan_id, files_processed=0, estimated_total=0, phase='scanning', current_file=''):
    """
    Update scan progress in Redis for the UI worker to read.

    Args:
        scan_id (str): The scan ID
        files_processed (int): Number of files processed
        estimated_total (int): Total files to process
        phase (str): Current scan phase
        current_file (str): Current file being processed
    """
    redis_client = get_redis_client()
    if not redis_client:
        logger.debug("Redis not available for progress updates")
        return

    progress_key = f"scan_progress:{scan_id}"
    progress_data = {
        'files_processed': str(files_processed),
        'estimated_total': str(estimated_total),
        'phase': phase,
        'current_file': current_file,
        'last_update': datetime.now(timezone.utc).isoformat()
    }

    try:
        result = redis_client.hset(progress_key, mapping=progress_data)
        redis_client.expire(progress_key, 3600)
        logger.info(f"Updated Redis progress for scan {scan_id}: {files_processed}/{estimated_total} in phase {phase}, key={progress_key}, result={result}")

        verify_data = redis_client.hgetall(progress_key)
        if verify_data:
            logger.debug(f"Verified Redis data for {scan_id}: {verify_data}")
        else:
            logger.error(f"Failed to verify Redis data for {scan_id} - key might not exist!")
    except Exception as e:
        logger.error(f"Failed to update Redis progress: {e}")
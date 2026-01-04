"""
Progress tracking utilities for PixelProbe
Shared between scan_service and tasks to avoid circular imports

v2.5.51: Added robust Redis connection handling with retry logic
v2.5.54: Added reset_redis_pool() for connection pool recovery
"""

import logging
import time
from datetime import datetime, timezone
from functools import wraps

logger = logging.getLogger(__name__)

# Connection pool to reuse connections
_redis_connection_pool = None


def reset_redis_pool():
    """
    Reset the global Redis connection pool.

    Call this when connections are persistently failing to force
    creation of a fresh connection pool on the next get_redis_client() call.

    v2.5.54: Added to recover from corrupted connection pools.
    """
    global _redis_connection_pool
    if _redis_connection_pool:
        try:
            _redis_connection_pool.disconnect()
            logger.info("Disconnected and reset Redis connection pool")
        except Exception as e:
            logger.warning(f"Error disconnecting Redis pool: {e}")
    _redis_connection_pool = None


def get_redis_client(retry_count=3, retry_delay=1.0):
    """
    Get Redis client from environment configuration with connection pooling and retry logic.

    Args:
        retry_count: Number of connection attempts before giving up
        retry_delay: Delay between retry attempts in seconds

    Returns:
        redis.Redis client or None if connection fails
    """
    import os
    import redis
    from redis.connection import ConnectionPool

    global _redis_connection_pool

    broker_url = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')

    # Parse redis://host:port/db format
    if not broker_url.startswith('redis://'):
        logger.error(f"Invalid Redis URL format: {broker_url}")
        return None

    url = broker_url.replace('redis://', '')
    parts = url.split('/')
    host_port = parts[0].split(':')
    host = host_port[0]
    port = int(host_port[1]) if len(host_port) > 1 else 6379
    db_num = int(parts[1]) if len(parts) > 1 else 0

    # Create connection pool if not exists
    if _redis_connection_pool is None:
        try:
            _redis_connection_pool = ConnectionPool(
                host=host,
                port=port,
                db=db_num,
                socket_timeout=5,
                socket_connect_timeout=5,
                socket_keepalive=True,
                retry_on_timeout=True,
                health_check_interval=30,
                max_connections=10
            )
        except Exception as e:
            logger.error(f"Failed to create Redis connection pool: {e}")
            _redis_connection_pool = None

    for attempt in range(retry_count):
        try:
            if _redis_connection_pool:
                client = redis.Redis(connection_pool=_redis_connection_pool)
            else:
                # Fallback to direct connection if pool creation failed
                client = redis.Redis(
                    host=host,
                    port=port,
                    db=db_num,
                    socket_timeout=5,
                    socket_connect_timeout=5,
                    socket_keepalive=True,
                    retry_on_timeout=True
                )

            # Verify connection is alive
            client.ping()
            return client

        except redis.ConnectionError as e:
            logger.warning(f"Redis connection attempt {attempt + 1}/{retry_count} failed: {e}")
            if attempt < retry_count - 1:
                time.sleep(retry_delay)
            else:
                logger.error(f"Failed to connect to Redis after {retry_count} attempts")
                # Reset connection pool on persistent failure
                _redis_connection_pool = None
                return None
        except Exception as e:
            logger.error(f"Unexpected error connecting to Redis: {e}")
            return None

    return None


def get_redis_info(info_section='memory'):
    """
    Safely get Redis server info with connection retry logic.

    Args:
        info_section: Redis INFO section to retrieve (e.g., 'memory', 'server')

    Returns:
        dict with Redis info or empty dict on failure
    """
    client = get_redis_client()
    if not client:
        return {}

    try:
        return client.info(info_section)
    except Exception as e:
        logger.warning(f"Failed to get Redis info ({info_section}): {e}")
        return {}


def with_redis_retry(max_retries=3, delay=0.5):
    """
    Decorator to add Redis operation retry logic.

    Args:
        max_retries: Maximum number of retry attempts
        delay: Delay between retries in seconds
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            import redis

            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (redis.ConnectionError, redis.TimeoutError, ConnectionResetError) as e:
                    last_exception = e
                    logger.warning(f"Redis operation {func.__name__} failed (attempt {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        time.sleep(delay)
                except Exception as e:
                    # Don't retry on non-connection errors
                    logger.error(f"Redis operation {func.__name__} failed with unexpected error: {e}")
                    raise

            # All retries exhausted
            logger.error(f"Redis operation {func.__name__} failed after {max_retries} attempts")
            if last_exception:
                raise last_exception
        return wrapper
    return decorator


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
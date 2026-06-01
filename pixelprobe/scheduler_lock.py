"""
Redis-based distributed lock for scheduler coordination across containers.

Uses Redis SETNX for atomic lock acquisition with a 60-second TTL for auto-recovery.
A heartbeat thread refreshes the lock every 30 seconds to keep it alive.
Falls back to file-based locking when Redis is unavailable.
"""

import os
import logging
import threading
import time
import socket
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LOCK_KEY = 'pixelprobe:scheduler:lock'


def parse_scheduler_lock(lock_value):
    """Parse lock value into (hostname, pid, timestamp_str).

    Lock formats:
    - New: "hostname:pid:timestamp" (e.g., "pixelprobe-app:123:2026-01-01T00:00:00+00:00")
    - Old: "pid:timestamp" (e.g., "123:2026-01-01T00:00:00+00:00")
    """
    parts = lock_value.split(':')
    if len(parts) >= 3 and not parts[0].isdigit():
        return parts[0], parts[1], ':'.join(parts[2:])
    return None, parts[0], ':'.join(parts[1:])


def should_force_acquire_lock(lock_hostname, lock_pid, lock_age,
                              my_hostname, my_pid, staleness_threshold=65):
    """Determine if we should force-acquire an existing lock.

    Returns (should_acquire: bool, reason: str).
    """
    if lock_hostname == my_hostname and lock_pid == my_pid:
        return True, "self-lock"
    if lock_hostname == my_hostname:
        if lock_age > staleness_threshold:
            return True, "stale-sibling"
        return False, "active-sibling"
    if lock_age > staleness_threshold:
        return True, "stale-remote"
    return False, "active-remote"


HEARTBEAT_INTERVAL_SECS = 30


def _start_heartbeat(lock_key, redis_client, container_hostname, scheduler_initialized):
    """Start a daemon thread that refreshes the scheduler lock periodically.

    Critically, a refresh failure is RETRIED indefinitely rather than breaking
    the loop. The previous implementation broke out on the first exception, so a
    single transient Redis blip permanently stopped refreshing and silently
    abandoned the lock. Now the loop keeps trying; when Redis recovers the next
    refresh re-establishes the TTL and the lock is never abandoned.

    A full Redis outage expires the lock for everyone (no sibling can acquire it
    either), so this process simply resumes ownership on recovery -- it does not
    shut the scheduler down (a BackgroundScheduler cannot be restarted, and in a
    single-container deployment there is no sibling to take over).
    """
    def heartbeat_loop():
        consecutive_failures = 0
        while scheduler_initialized[0]:
            time.sleep(HEARTBEAT_INTERVAL_SECS)
            if not scheduler_initialized[0]:
                break
            try:
                refresh_value = f"{container_hostname}:{os.getpid()}:{datetime.now(timezone.utc).isoformat()}"
                redis_client.set(lock_key, refresh_value, ex=60)
                if consecutive_failures:
                    logger.info(
                        f"Scheduler lock refresh recovered after "
                        f"{consecutive_failures} failure(s) in process {os.getpid()}"
                    )
                consecutive_failures = 0
                logger.debug(f"Refreshed scheduler lock in process {os.getpid()}")
            except Exception as e:
                consecutive_failures += 1
                logger.warning(
                    f"Failed to refresh scheduler lock "
                    f"(consecutive failure {consecutive_failures}), will keep retrying: {e}"
                )

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    logger.info(f"Started scheduler lock heartbeat thread in process {os.getpid()}")
    return heartbeat_thread


def _try_acquire_and_init(redis_client, lock_key, lock_value, container_hostname,
                          scheduler, app, scheduler_initialized):
    """Acquire the lock, initialize the scheduler, and start heartbeat."""
    scheduler.init_app(app)
    scheduler_initialized[0] = True
    app.scheduler_redis_lock_key = lock_key
    _start_heartbeat(lock_key, redis_client, container_hostname, scheduler_initialized)


def _start_retry_thread(redis_client, lock_key, container_hostname,
                        scheduler, app, scheduler_initialized):
    """Start background retry thread for stale lock recovery."""
    def retry_scheduler_lock():
        retry_count = 0
        max_retries = 10

        while not scheduler_initialized[0] and retry_count < max_retries:
            time.sleep(30)
            retry_count += 1

            try:
                current_lock = redis_client.get(lock_key)
                new_value = f"{container_hostname}:{os.getpid()}:{datetime.now(timezone.utc).isoformat()}"

                if not current_lock:
                    retry_acquired = redis_client.set(lock_key, new_value, nx=True, ex=60)
                    if retry_acquired:
                        logger.info(f"Retry #{retry_count}: Acquired scheduler lock, initializing scheduler")
                        with app.app_context():
                            _try_acquire_and_init(
                                redis_client, lock_key, new_value, container_hostname,
                                scheduler, app, scheduler_initialized
                            )
                        break
                else:
                    lock_str = current_lock.decode('utf-8') if isinstance(current_lock, bytes) else current_lock
                    retry_hostname, retry_pid, retry_ts_str = parse_scheduler_lock(lock_str)
                    lock_ts = datetime.fromisoformat(retry_ts_str)
                    current_age = (datetime.now(timezone.utc) - lock_ts).total_seconds()

                    retry_should_acquire, retry_reason = should_force_acquire_lock(
                        retry_hostname, retry_pid, current_age,
                        container_hostname, str(os.getpid())
                    )

                    if retry_should_acquire:
                        redis_client.set(lock_key, new_value, ex=60)
                        logger.info(f"Retry #{retry_count}: Acquired lock (reason={retry_reason}, age={current_age:.0f}s), initializing scheduler")
                        with app.app_context():
                            _try_acquire_and_init(
                                redis_client, lock_key, new_value, container_hostname,
                                scheduler, app, scheduler_initialized
                            )
                        break
                    else:
                        logger.debug(f"Retry #{retry_count}: Lock still held (reason={retry_reason}, age={current_age:.0f}s)")
            except Exception as retry_err:
                logger.warning(f"Retry #{retry_count} failed: {retry_err}")

        if not scheduler_initialized[0]:
            logger.warning("Scheduler lock retry exhausted - another process must have it")

    retry_thread = threading.Thread(target=retry_scheduler_lock, daemon=True)
    retry_thread.start()
    logger.info(f"Started scheduler lock retry thread in process {os.getpid()}")


def initialize_scheduler_with_lock(app, scheduler):
    """Initialize the scheduler using a Redis distributed lock (or file lock fallback).

    Returns True if the scheduler was initialized in this process.
    """
    from pixelprobe.progress_utils import get_redis_client

    redis_client = get_redis_client()
    scheduler_initialized = [False]
    container_hostname = socket.gethostname()

    if redis_client:
        try:
            lock_value = f"{container_hostname}:{os.getpid()}:{datetime.now(timezone.utc).isoformat()}"
            acquired = redis_client.set(LOCK_KEY, lock_value, nx=True, ex=60)

            if acquired:
                logger.info(f"Acquired Redis scheduler lock in process {os.getpid()}, initializing scheduler")
                _try_acquire_and_init(
                    redis_client, LOCK_KEY, lock_value, container_hostname,
                    scheduler, app, scheduler_initialized
                )
            else:
                existing = redis_client.get(LOCK_KEY)
                if existing:
                    existing = existing.decode('utf-8') if isinstance(existing, bytes) else existing
                    try:
                        lock_hostname, lock_pid, lock_timestamp_str = parse_scheduler_lock(existing)
                        lock_timestamp = datetime.fromisoformat(lock_timestamp_str)
                        lock_age = (datetime.now(timezone.utc) - lock_timestamp).total_seconds()

                        should_acquire, reason = should_force_acquire_lock(
                            lock_hostname, lock_pid, lock_age,
                            container_hostname, str(os.getpid())
                        )

                        if should_acquire:
                            logger.warning(f"Acquiring scheduler lock (reason={reason}, age={lock_age:.0f}s, holder={existing})")
                            redis_client.set(LOCK_KEY, lock_value, ex=60)
                            logger.info(f"Acquired scheduler lock in process {os.getpid()}, initializing scheduler")
                            _try_acquire_and_init(
                                redis_client, LOCK_KEY, lock_value, container_hostname,
                                scheduler, app, scheduler_initialized
                            )
                        else:
                            logger.info(f"Scheduler lock held by sibling/remote (reason={reason}, holder={existing}, age={lock_age:.0f}s), skipping in process {os.getpid()}")
                            _start_retry_thread(
                                redis_client, LOCK_KEY, container_hostname,
                                scheduler, app, scheduler_initialized
                            )
                    except Exception as parse_err:
                        logger.info(f"Scheduler already running (lock held by: {existing}), skipping in process {os.getpid()}")

        except Exception as e:
            logger.warning(f"Redis lock failed ({e}), falling back to file lock")
            redis_client = None

    # Fallback to file lock if Redis unavailable
    if not redis_client and not scheduler_initialized[0]:
        import fcntl
        scheduler_lock_file = '/tmp/pixelprobe_scheduler.lock'

        try:
            lock_file = open(scheduler_lock_file, 'w')
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            logger.info(f"Acquired file scheduler lock in process {os.getpid()}, initializing scheduler (Redis unavailable)")
            scheduler.init_app(app)
            app.scheduler_lock_file = lock_file
            scheduler_initialized[0] = True

        except (IOError, OSError):
            logger.info(f"Scheduler already running in another process, skipping initialization in process {os.getpid()}")

    return scheduler_initialized[0]

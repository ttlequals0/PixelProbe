"""
Redis-based distributed lock for scheduler coordination across containers.

Exactly one process across all containers may own the scheduler. Ownership is
claimed strictly via SET NX with a 60-second TTL; a dead holder is recovered by
TTL expiry, which standby processes pick up in their retry loop. There is no
force-acquire heuristic: hostname/pid are not reliable identity (containers in
a podman pod share a hostname while pids collide across pid namespaces), so the
lock value carries a per-process uuid and ownership checks compare the exact
value. Falls back to file-based locking when Redis is unavailable.
"""

import os
import logging
import threading
import time
import socket
import uuid

logger = logging.getLogger(__name__)

LOCK_KEY = 'pixelprobe:scheduler:lock'
LOCK_TTL_SECS = 60
HEARTBEAT_INTERVAL_SECS = 30
# After a failed refresh, retry quickly: waiting a full interval would leave
# only one attempt before the TTL lapses and a standby can take the lock.
HEARTBEAT_FAILURE_RETRY_SECS = 5
RETRY_INTERVAL_SECS = 30

# Refresh the TTL only if this process still holds the lock.
_REFRESH_IF_OWNER_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


def scheduler_enabled():
    """Whether this process may run the scheduler (SCHEDULER_ENABLED env var)."""
    return os.environ.get('SCHEDULER_ENABLED', 'true').strip().lower() not in ('false', '0', 'no')


def make_lock_value():
    """Unique per-process lock value: hostname and pid for log readability,
    uuid for actual identity."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"


def _start_heartbeat(lock_key, redis_client, lock_value, scheduler_initialized):
    """Start a daemon thread that refreshes the scheduler lock periodically.

    The refresh is an atomic compare-and-expire: it only extends the TTL while
    this process still holds the lock, so it can never overwrite another
    holder. If the key vanished (a Redis outage expires it for everyone), the
    thread reclaims it with SET NX. If another process claimed it in the
    meantime, we log loudly but keep the scheduler running -- shutting a
    BackgroundScheduler down is one-way (it cannot be started again), and in a
    single-container deployment that would leave no scheduler at all. (A
    future refinement could pause() here and resume() on reclaim.)

    A refresh failure is RETRIED indefinitely rather than breaking the loop;
    a single transient Redis blip must not silently abandon the lock.
    """
    def heartbeat_loop():
        consecutive_failures = 0
        while scheduler_initialized[0]:
            time.sleep(HEARTBEAT_FAILURE_RETRY_SECS if consecutive_failures
                       else HEARTBEAT_INTERVAL_SECS)
            if not scheduler_initialized[0]:
                break
            try:
                refreshed = redis_client.eval(
                    _REFRESH_IF_OWNER_SCRIPT, 1, lock_key, lock_value, LOCK_TTL_SECS
                )
                if not refreshed:
                    reclaimed = redis_client.set(lock_key, lock_value, nx=True, ex=LOCK_TTL_SECS)
                    if reclaimed:
                        logger.info(
                            f"Scheduler lock had expired; reclaimed in process {os.getpid()}"
                        )
                    else:
                        holder = redis_client.get(lock_key)
                        holder = holder.decode('utf-8') if isinstance(holder, bytes) else holder
                        logger.warning(
                            f"Scheduler lock now held by {holder} but process "
                            f"{os.getpid()} is still running a scheduler -- "
                            f"duplicate scheduled runs are possible until one restarts"
                        )
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


def _init_scheduler_and_heartbeat(redis_client, lock_key, lock_value,
                                  scheduler, app, scheduler_initialized):
    """Initialize the scheduler and start the lock heartbeat."""
    scheduler.init_app(app)
    scheduler_initialized[0] = True
    app.scheduler_redis_lock_key = lock_key
    _start_heartbeat(lock_key, redis_client, lock_value, scheduler_initialized)


def _start_retry_thread(redis_client, lock_key, lock_value,
                        scheduler, app, scheduler_initialized):
    """Start a standby thread that takes over when the holder's lock expires.

    Retries indefinitely: failover must work whenever the holder dies, not just
    during the first few minutes after boot. Acquisition is SET NX only -- a
    live lock is never stolen.
    """
    def retry_scheduler_lock():
        retry_count = 0
        while not scheduler_initialized[0]:
            time.sleep(RETRY_INTERVAL_SECS)
            retry_count += 1
            try:
                # Our own writes always carry a TTL; a persistent key (manual
                # redis-cli SET, foreign writer) would otherwise deadlock every
                # standby forever. Give it a TTL so normal expiry recovery applies.
                if redis_client.ttl(lock_key) == -1:
                    logger.warning(f"Scheduler lock key has no TTL, applying {LOCK_TTL_SECS}s expiry")
                    redis_client.expire(lock_key, LOCK_TTL_SECS)

                acquired = redis_client.set(lock_key, lock_value, nx=True, ex=LOCK_TTL_SECS)
                if acquired:
                    logger.info(
                        f"Retry #{retry_count}: Acquired scheduler lock, initializing scheduler"
                    )
                    with app.app_context():
                        _init_scheduler_and_heartbeat(
                            redis_client, lock_key, lock_value,
                            scheduler, app, scheduler_initialized
                        )
                    break
            except Exception as retry_err:
                logger.warning(f"Scheduler lock retry #{retry_count} failed: {retry_err}")

    retry_thread = threading.Thread(target=retry_scheduler_lock, daemon=True)
    retry_thread.start()
    logger.info(f"Started scheduler lock retry thread in process {os.getpid()}")


def initialize_scheduler_with_lock(app, scheduler):
    """Initialize the scheduler using a Redis distributed lock (or file lock fallback).

    Returns True if the scheduler was initialized in this process.
    """
    from pixelprobe.progress_utils import get_redis_client

    if not scheduler_enabled():
        # Lets a deployment pin the scheduler to one container (e.g. false on
        # web, default true on the celery worker) instead of having every
        # process race for the distributed lock.
        logger.info(f"SCHEDULER_ENABLED is false, skipping scheduler initialization in process {os.getpid()}")
        return False

    redis_client = get_redis_client()
    scheduler_initialized = [False]
    lock_value = make_lock_value()

    if redis_client:
        try:
            acquired = redis_client.set(LOCK_KEY, lock_value, nx=True, ex=LOCK_TTL_SECS)

            if acquired:
                logger.info(f"Acquired Redis scheduler lock in process {os.getpid()}, initializing scheduler")
                _init_scheduler_and_heartbeat(
                    redis_client, LOCK_KEY, lock_value,
                    scheduler, app, scheduler_initialized
                )
            else:
                holder = redis_client.get(LOCK_KEY)
                holder = holder.decode('utf-8') if isinstance(holder, bytes) else holder
                logger.info(
                    f"Scheduler lock held by {holder}, standing by in process {os.getpid()}"
                )
                _start_retry_thread(
                    redis_client, LOCK_KEY, lock_value,
                    scheduler, app, scheduler_initialized
                )

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

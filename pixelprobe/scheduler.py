import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pixelprobe.models import db, ScanSchedule, ScanResult, ScanState, HealthcheckConfig, ScanReport
from pixelprobe.constants import SCAN_PHASES, TERMINAL_SCAN_PHASES
from pixelprobe.services.scan_engine import maybe_finalize_scan
from pixelprobe.utils.paths import is_path_under
from sqlalchemy import text
import threading
import requests
from pixelprobe.services.healthcheck_service import HealthcheckService

logger = logging.getLogger(__name__)


def compute_next_interval_run(last_run, stored_next_run, unit, value, now=None):
    """Next fire time for an interval schedule surviving an app restart.

    Returns None when there is no history (caller lets APScheduler default to
    now + interval). Naive datetimes are treated as UTC.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if last_run is None:
        return None

    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)

    next_run = last_run + timedelta(**{unit: value})
    if next_run >= now:
        return next_run

    if stored_next_run is not None:
        if stored_next_run.tzinfo is None:
            stored_next_run = stored_next_run.replace(tzinfo=timezone.utc)
        if stored_next_run > now:
            return stored_next_run

    return now + timedelta(**{unit: value})


class MediaScheduler:
    # Defaults for queue-on-conflict retry when a scheduled scan fires while
    # another scan is still running. Up to MAX_COUNT retries spaced
    # DELAY_MINUTES apart; after that we give up until the next cron fire.
    DEFAULT_RETRY_DELAY_MINUTES = 10
    DEFAULT_RETRY_MAX_COUNT = 144

    def __init__(self, app=None):
        self.scheduler = BackgroundScheduler()
        self.app = app
        self.scan_lock = threading.Lock()
        self.cleanup_lock = threading.Lock()
        self.excluded_paths = []
        self.excluded_extensions = []

        self.pending_retries: Dict[str, int] = {}
        self._retry_lock = threading.Lock()
        self.retry_delay_minutes = self._load_positive_int_env(
            'SCHEDULE_RETRY_DELAY_MINUTES', self.DEFAULT_RETRY_DELAY_MINUTES, min_value=1
        )
        self.retry_max_count = self._load_positive_int_env(
            'SCHEDULE_RETRY_MAX_COUNT', self.DEFAULT_RETRY_MAX_COUNT, min_value=0
        )

        # Load exclusions from environment
        self._load_exclusions()

    @staticmethod
    def _load_positive_int_env(var_name: str, default: int, min_value: int) -> int:
        raw = os.environ.get(var_name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.warning(
                f"Invalid {var_name}={raw!r}; falling back to default {default}"
            )
            return default
        return max(min_value, value)

    def _queue_conflict_retry(self, retry_key: str, retry_func, retry_args, reason: str):
        """Queue a one-shot retry when a scheduled scan's cron fire is skipped.

        APScheduler consumes the original cron fire when the guard trips, so
        without a retry the schedule is silently dropped until its next regular
        fire (e.g. a weekly cleanup would go missing for an entire week).
        """
        run_date = datetime.now(timezone.utc) + timedelta(minutes=self.retry_delay_minutes)
        # Hold the lock across add_job so the counter can't drift if two callers
        # race on the same retry_key. add_job on the default MemoryJobStore is
        # in-process and cheap, so the critical section stays short.
        with self._retry_lock:
            count = self.pending_retries.get(retry_key, 0)
            if count >= self.retry_max_count:
                logger.warning(
                    f"{retry_key} skipped ({reason}); already retried "
                    f"{count} times, giving up until next cron fire"
                )
                self.pending_retries.pop(retry_key, None)
                return
            count += 1
            job_id = f"{retry_key}_retry_{count}"
            try:
                self.scheduler.add_job(
                    func=retry_func,
                    args=list(retry_args),
                    trigger='date',
                    run_date=run_date,
                    id=job_id,
                    max_instances=1,
                    replace_existing=True,
                )
            except Exception as e:
                logger.error(
                    f"Failed to queue retry job {job_id}: {e}", exc_info=True
                )
                return
            self.pending_retries[retry_key] = count

        logger.warning(
            f"{retry_key} skipped ({reason}); queued retry "
            f"#{count}/{self.retry_max_count} at {run_date.isoformat()}"
        )

    def _clear_pending_retry(self, retry_key: str):
        with self._retry_lock:
            self.pending_retries.pop(retry_key, None)

    def _filter_excluded_paths(self, scan_paths):
        """Filter out excluded paths and return the remaining ones."""
        return [
            p.strip() for p in scan_paths
            if not any(is_path_under(p.strip(), exc) for exc in self.excluded_paths)
        ]

    def _execute_scan_request(self, endpoint, payload, scan_label, timeout=30):
        """Execute an HTTP scan request against the local API.

        Args:
            endpoint: API path (e.g. '/api/scan')
            payload: JSON payload dict
            scan_label: Human-readable label for logging (e.g. 'Periodic scan')
            timeout: Request timeout in seconds

        Returns:
            The requests.Response object, or None on connection error.
        """
        base_url = self._get_api_base_url()
        headers = {
            'X-Internal-Secret': self.app.config.get('INTERNAL_API_SECRET', ''),
            'Content-Type': 'application/json'
        }
        try:
            response = requests.post(
                f'{base_url}{endpoint}',
                json=payload,
                headers=headers,
                timeout=timeout
            )
            if response.status_code == 200:
                logger.info(f"{scan_label} started successfully")
            elif response.status_code == 409:
                logger.warning(f"{scan_label} skipped - another scan is already running")
            else:
                logger.error(f"{scan_label} API call failed: {response.status_code} - {response.text}")
            return response
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to call API for {scan_label}: {e}")
            return None

    def _send_healthcheck_start(self, schedule_id):
        """Send the healthcheck start ping for a schedule, if configured.

        Called only after a scan is confirmed started, so a conflicted/failed
        fire never leaves a dangling 'start' with no matching completion (which
        would trip a false 'down' alert), and a retry does not re-ping.
        """
        healthcheck_config = HealthcheckConfig.query.filter_by(schedule_id=schedule_id).first()
        if not (healthcheck_config and healthcheck_config.is_active
                and healthcheck_config.send_start_ping):
            return
        try:
            success = HealthcheckService().ping_start(healthcheck_config.healthcheck_url)
            healthcheck_config.last_ping_status = 'success' if success else 'failure'
            healthcheck_config.last_ping_time = datetime.now(timezone.utc)
            db.session.commit()
            logger.info(f"Healthcheck start ping sent for schedule {schedule_id}: "
                        f"{'success' if success else 'failure'}")
        except Exception as e:
            logger.error(f"Failed to send healthcheck start ping for schedule {schedule_id}: {e}")

    def _handle_scan_response(self, retry_key, retry_func, retry_args, response):
        """Clear the retry counter on a confirmed start, or queue a retry on a
        conflict/failure so the cron fire is not silently dropped.

        Returns True only if the scan actually started (HTTP 200). A 409 (a scan
        slipped in during the check->POST window) or a connection error/unexpected
        status both queue a retry rather than losing the fire.
        """
        status_code = response.status_code if response is not None else None
        if status_code == 200:
            self._clear_pending_retry(retry_key)
            return True
        reason = "api returned 409" if status_code == 409 else f"api status={status_code}"
        self._queue_conflict_retry(retry_key, retry_func, retry_args, reason)
        return False

    def _get_api_base_url(self):
        """
        Get the base URL for API calls.
        In Docker, celery-worker needs to call the main app via service name.
        """
        # Allow explicit override via environment
        api_url = os.environ.get('API_BASE_URL')
        if api_url:
            return api_url

        port = self.app.config.get('PORT', os.environ.get('PORT', 5000)) if self.app else 5000

        # Check if we're in Docker by looking for common indicators
        # In Docker, localhost won't reach the main app - use service name
        if os.path.exists('/.dockerenv') or os.environ.get('CELERY_BROKER_URL'):
            # Use Docker service name (from docker-compose)
            return f'http://pixelprobe:{port}'

        return f'http://localhost:{port}'
        
    def init_app(self, app):
        self.app = app
        self.scheduler.start()
        
        # Schedule default tasks from environment variables
        self._schedule_default_tasks()
        
        # Schedule daily log retention cleanup at 3 AM
        self.scheduler.add_job(
            func=self._run_log_cleanup,
            trigger="cron",
            hour=3,
            minute=0,
            id="log_retention_cleanup",
            name="Clean up old log entries",
            misfire_grace_time=3600
        )
        logger.info("Scheduled daily log retention cleanup at 03:00")

        # Schedule daily DATA retention cleanup at 4 AM. Replaces the Celery beat
        # entry that was configured but never launched (no beat process ran), so
        # the retention task never executed and the DB grew unbounded.
        self.scheduler.add_job(
            func=self._run_retention_cleanup,
            trigger="cron",
            hour=4,
            minute=0,
            id="data_retention_cleanup",
            name="Clean up data per retention policy",
            misfire_grace_time=3600,
            coalesce=True
        )
        logger.info("Scheduled daily data retention cleanup at 04:00")

        # Schedule stuck scan detection every 5 minutes
        self.scheduler.add_job(
            func=self._check_stuck_scans,
            trigger="interval",
            minutes=5,
            id="stuck_scan_checker",
            name="Check for stuck scans",
            misfire_grace_time=60
        )
        logger.info("Scheduled stuck scan detection to run every 5 minutes")
        
        # Load saved schedules from database
        with app.app_context():
            self._load_saved_schedules()
            
    def _load_exclusions(self):
        """Load path and extension exclusions from environment variables"""
        excluded_paths_env = os.environ.get('EXCLUDED_PATHS', '')
        if excluded_paths_env:
            self.excluded_paths = [p.strip() for p in excluded_paths_env.split(',') if p.strip()]
            
        excluded_extensions_env = os.environ.get('EXCLUDED_EXTENSIONS', '')
        if excluded_extensions_env:
            self.excluded_extensions = [e.strip().lower() for e in excluded_extensions_env.split(',') if e.strip()]
            
    def _schedule_default_tasks(self):
        """Schedule default tasks based on environment variables"""
        # Periodic scan schedule
        scan_schedule = os.environ.get('PERIODIC_SCAN_SCHEDULE', '')
        if scan_schedule:
            try:
                if scan_schedule.startswith('cron:'):
                    # Cron format: cron:0 2 * * *
                    cron_expr = scan_schedule[5:]
                    self._add_cron_job('default_scan', self._run_periodic_scan, cron_expr)
                elif scan_schedule.startswith('interval:'):
                    # Interval format: interval:hours:6
                    parts = scan_schedule.split(':')
                    if len(parts) == 3:
                        unit = parts[1]
                        value = int(parts[2])
                        self._add_interval_job('default_scan', self._run_periodic_scan, unit, value)
                logger.info(f"Scheduled periodic scan: {scan_schedule}")
            except Exception as e:
                logger.error(f"Failed to schedule periodic scan: {e}")
                
        # Cleanup schedule
        cleanup_schedule = os.environ.get('CLEANUP_SCHEDULE', '')
        if cleanup_schedule:
            try:
                if cleanup_schedule.startswith('cron:'):
                    cron_expr = cleanup_schedule[5:]
                    self._add_cron_job('default_cleanup', self._run_cleanup, cron_expr)
                elif cleanup_schedule.startswith('interval:'):
                    parts = cleanup_schedule.split(':')
                    if len(parts) == 3:
                        unit = parts[1]
                        value = int(parts[2])
                        self._add_interval_job('default_cleanup', self._run_cleanup, unit, value)
                logger.info(f"Scheduled cleanup: {cleanup_schedule}")
            except Exception as e:
                logger.error(f"Failed to schedule cleanup: {e}")
                
    def _add_cron_job(self, job_id: str, func, cron_expr: str):
        """Add a cron-based job"""
        # Parse cron expression (minute hour day month day_of_week)
        parts = cron_expr.split()
        if len(parts) != 5:
            raise ValueError("Invalid cron expression")
            
        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4]
        )
        
        self.scheduler.add_job(
            func,
            trigger,
            id=job_id,
            replace_existing=True,
            # Still run a fire that was missed during a brief scheduler restart
            # (deploys take ~30s); coalesce collapses several missed fires into one.
            misfire_grace_time=3600,
            coalesce=True
        )

    def _add_interval_job(self, job_id: str, func, unit: str, value: int,
                          next_run_time=None):
        """Add an interval-based job, optionally with an explicit first fire time"""
        kwargs = {unit: value}
        trigger = IntervalTrigger(**kwargs)

        job_kwargs = {
            'id': job_id,
            'replace_existing': True,
            'misfire_grace_time': 3600,
            'coalesce': True,
        }
        if next_run_time is not None:
            job_kwargs['next_run_time'] = next_run_time

        self.scheduler.add_job(func, trigger, **job_kwargs)
        
    def _load_saved_schedules(self):
        """Load and activate saved schedules from database"""
        try:
            # Debug: log all schedules in database before filtering
            all_schedules = ScanSchedule.query.all()
            logger.info(f"Total schedules in database: {len(all_schedules)}")
            for s in all_schedules:
                logger.info(f"  Schedule '{s.name}' (id={s.id}): is_active={s.is_active}, cron={s.cron_expression}")

            schedules = ScanSchedule.query.filter_by(is_active=True).all()
            logger.info(f"Active schedules to load: {len(schedules)}")
            
            # Deduplicate schedules; commit deactivations once after the loop
            # (committing mid-iteration can expire the objects being iterated)
            seen_schedules = {}
            duplicates = []
            for schedule in schedules:
                key = f"{schedule.cron_expression}:{schedule.scan_paths}:{schedule.scan_type}"

                if key in seen_schedules:
                    logger.warning(f"Deactivating duplicate schedule {schedule.id}: {schedule.name}")
                    schedule.is_active = False
                    duplicates.append(schedule.id)
                    continue

                seen_schedules[key] = schedule

            if duplicates:
                db.session.commit()

            for schedule in seen_schedules.values():
                self._activate_schedule(schedule)
        except Exception as e:
            # Roll back so a query/commit failure here doesn't leave the session
            # in an aborted state for later startup steps.
            db.session.rollback()
            logger.error(f"Failed to load saved schedules: {e}")
            
    def _activate_schedule(self, schedule: ScanSchedule):
        """Activate a scan schedule"""
        try:
            job_id = f"schedule_{schedule.id}"
            schedule_id = schedule.id  # Store the ID, not the object

            # Create job function with schedule ID (not the object to avoid detached instance)
            def job_func():
                self._run_scheduled_scan(schedule_id)

            # Interval schedules preserve their cadence across app restarts:
            # the next fire is computed BEFORE add_job (no post-hoc job.modify)
            if schedule.cron_expression.startswith('interval:'):
                parts = schedule.cron_expression.split(':')
                if len(parts) != 3:
                    raise ValueError(f"Invalid interval format: {schedule.cron_expression}")
                unit = parts[1]
                value = int(parts[2])
                next_run_time = compute_next_interval_run(
                    schedule.last_run, schedule.next_run, unit, value
                )
                self._add_interval_job(job_id, job_func, unit, value,
                                       next_run_time=next_run_time)
            else:
                # Standard cron format
                self._add_cron_job(job_id, job_func, schedule.cron_expression)

            # Persist what APScheduler actually scheduled
            job = self.scheduler.get_job(job_id)
            if job:
                schedule.next_run = job.next_run_time
                db.session.commit()

            logger.info(f"Activated schedule: {schedule.name} (next run: {schedule.next_run})")
        except Exception as e:
            logger.error(f"Failed to activate schedule {schedule.name}: {e}")
            
    def _run_periodic_scan(self):
        """Run periodic scan on configured paths via HTTP self-call"""
        if not self.scan_lock.acquire(blocking=False):
            # Another scheduled scan holds the lock; queue a retry so this fire
            # is not silently dropped (APScheduler consumes the original fire).
            self._queue_conflict_retry(
                'periodic', self._run_periodic_scan, (),
                "scan_lock held by another scheduled scan"
            )
            return

        try:
            with self.app.app_context():
                # Check if ANY scan is already running before proceeding
                scan_state = ScanState.get_or_create()
                if scan_state.is_active and scan_state.phase not in TERMINAL_SCAN_PHASES:
                    self._queue_conflict_retry(
                        'periodic', self._run_periodic_scan, (),
                        f"phase={scan_state.phase}"
                    )
                    return

                from pixelprobe.utils.helpers import get_configured_scan_paths
                scan_paths = get_configured_scan_paths()

                logger.info(f"Starting periodic scan of paths: {scan_paths}")

                filtered_paths = self._filter_excluded_paths(scan_paths)
                if not filtered_paths:
                    logger.warning("No paths to scan after exclusions")
                    # Not a conflict -- this fire is resolved, clear any retry.
                    self._clear_pending_retry('periodic')
                    return

                response = self._execute_scan_request(
                    '/api/scan',
                    {
                        'scan_type': 'full',
                        'directories': filtered_paths,
                        'force_rescan': False,
                        'source': 'scheduled_periodic'
                    },
                    'Periodic scan',
                    timeout=30
                )
                self._handle_scan_response('periodic', self._run_periodic_scan, (), response)

        except Exception as e:
            logger.error(f"Failed to run periodic scan: {e}")
        finally:
            self.scan_lock.release()
            
    def _run_scheduled_scan(self, schedule_id: int):
        """Run a scheduled scan via HTTP self-call to avoid Flask context issues"""
        retry_key = f"schedule_{schedule_id}"
        if not self.scan_lock.acquire(blocking=False):
            # Another scheduled scan holds the lock (e.g. two crons firing at the
            # same instant). Queue a retry so this fire is not silently dropped.
            self._queue_conflict_retry(
                retry_key, self._run_scheduled_scan, (schedule_id,),
                "scan_lock held by another scheduled scan"
            )
            return

        try:
            with self.app.app_context():
                # Check if ANY scan is already running before proceeding
                scan_state = ScanState.get_or_create()
                if scan_state.is_active and scan_state.phase not in TERMINAL_SCAN_PHASES:
                    self._queue_conflict_retry(
                        retry_key, self._run_scheduled_scan, (schedule_id,),
                        f"phase={scan_state.phase}"
                    )
                    return

                schedule = db.session.get(ScanSchedule, schedule_id)
                if not schedule or not schedule.is_active:
                    # Not a conflict -- this fire is resolved, clear any retry.
                    self._clear_pending_retry(retry_key)
                    return

                # Cache schedule attributes BEFORE any commits/HTTP call.
                # SQLAlchemy expires objects after commit(), and lazy-loading can fail
                # in the celery worker context, returning None for these attributes.
                cached_scan_type = getattr(schedule, 'scan_type', 'normal')
                cached_schedule_name = schedule.name
                cached_force_rescan = getattr(schedule, 'force_rescan', False)

                from pixelprobe.utils.helpers import get_configured_scan_paths
                scan_paths = get_configured_scan_paths()

                if not scan_paths:
                    logger.error(f"Scheduled scan {schedule_id}: No scan paths configured in database or SCAN_PATHS env var!")
                    self._clear_pending_retry(retry_key)
                    return

                scan_type = cached_scan_type

                logger.info(f"Running scheduled scan '{cached_schedule_name}' (type: {scan_type}) on paths: {scan_paths}")

                filtered_paths = self._filter_excluded_paths(scan_paths)
                if not filtered_paths:
                    logger.error(f"Scheduled scan {schedule_id} has no valid paths after filtering. "
                                 f"SCAN_PATHS={scan_paths}, excluded_paths={self.excluded_paths}")
                    self._clear_pending_retry(retry_key)
                    return

                scan_label = f"Scheduled scan {schedule_id}"
                if scan_type == 'orphan':
                    response = self._execute_scan_request(
                        '/api/cleanup-orphaned',
                        {'schedule_id': schedule_id},
                        scan_label, timeout=60
                    )
                elif scan_type == 'file_changes':
                    response = self._execute_scan_request(
                        '/api/file-changes',
                        {'schedule_id': schedule_id},
                        scan_label, timeout=60
                    )
                else:
                    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
                    response = self._execute_scan_request(
                        '/api/scan',
                        {
                            'scan_type': 'full',
                            'directories': filtered_paths,
                            'force_rescan': cached_force_rescan,
                            'source': f'scheduled_{schedule_id}_{timestamp}'
                        },
                        scan_label, timeout=60
                    )

                # Only advance last_run/next_run once the scan actually started.
                # A failed or conflicted (409) fire is retried instead of being
                # recorded as a successful run that skips ahead a full interval.
                if self._handle_scan_response(retry_key, self._run_scheduled_scan, (schedule_id,), response):
                    # Send the start ping now that the scan is confirmed started.
                    self._send_healthcheck_start(schedule_id)
                    # Re-fetch the row: the object above may be expired after the
                    # ping commit / the up-to-60s HTTP call.
                    fresh = db.session.get(ScanSchedule, schedule_id)
                    if fresh:
                        fresh.last_run = datetime.now(timezone.utc)
                        job_id = f"schedule_{schedule_id}"
                        for job in self.scheduler.get_jobs():
                            if job.id == job_id:
                                fresh.next_run = job.next_run_time
                                break
                        db.session.commit()

        except Exception as e:
            logger.error(f"Failed to run scheduled scan {schedule_id}: {e}")
        finally:
            self.scan_lock.release()
            
    def _run_cleanup(self):
        """Run cleanup of orphaned records via HTTP self-call"""
        if not self.cleanup_lock.acquire(blocking=False):
            logger.warning("Cleanup already in progress, skipping")
            return
            
        try:
            with self.app.app_context():
                logger.info("Starting scheduled cleanup of orphaned records")

                # Unified handling: clears the retry on a confirmed start (200),
                # and queues a retry on 409 / connection error / other failure,
                # matching scheduled and periodic scans.
                response = self._execute_scan_request(
                    '/api/cleanup-orphaned', {}, 'Cleanup', timeout=60
                )
                self._handle_scan_response('default_cleanup', self._run_cleanup, (), response)

        except Exception as e:
            logger.error(f"Failed to run cleanup: {e}")
        finally:
            self.cleanup_lock.release()
            
    def create_schedule(self, name: str, cron_expression: str, scan_paths: List[str] = None, scan_type: str = 'normal') -> ScanSchedule:
        """Create a new scan schedule"""
        # Store the original expression (could be cron or interval format)
        schedule = ScanSchedule(
            name=name,
            cron_expression=cron_expression,
            scan_paths=json.dumps(scan_paths) if scan_paths else None,
            scan_type=scan_type,
            is_active=True
        )
        
        db.session.add(schedule)
        db.session.commit()
        
        # Activate the schedule
        self._activate_schedule(schedule)
        
        return schedule
        
    def update_schedule(self, schedule_id: int, **kwargs) -> ScanSchedule:
        """Update an existing schedule"""
        schedule = db.session.get(ScanSchedule, schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")

        # Remove old job
        job_id = f"schedule_{schedule_id}"
        try:
            self.scheduler.remove_job(job_id)
        except Exception as e:
            logger.error(f"Error in scheduled task: {e}")
            
        # Update schedule
        for key, value in kwargs.items():
            if hasattr(schedule, key):
                if key == 'scan_paths' and isinstance(value, list):
                    value = json.dumps(value)
                setattr(schedule, key, value)
                
        db.session.commit()
        
        # Reactivate if still active
        if schedule.is_active:
            self._activate_schedule(schedule)
            
        return schedule
        
    def delete_schedule(self, schedule_id: int):
        """Delete a schedule"""
        schedule = db.session.get(ScanSchedule, schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")

        # Remove job
        job_id = f"schedule_{schedule_id}"
        try:
            self.scheduler.remove_job(job_id)
        except Exception as e:
            logger.error(f"Error in scheduled task: {e}")
            
        db.session.delete(schedule)
        db.session.commit()
        
    def get_schedule_status(self) -> Dict:
        """Get status of all schedules"""
        schedules = []
        
        for schedule in ScanSchedule.query.all():
            job_id = f"schedule_{schedule.id}"
            job = self.scheduler.get_job(job_id)
            
            schedule_info = schedule.to_dict()
            schedule_info['job_active'] = job is not None
            if job:
                schedule_info['next_run'] = job.next_run_time.isoformat() if job.next_run_time else None
                
            schedules.append(schedule_info)
            
        return {
            'schedules': schedules,
            'scheduler_running': self.scheduler.running
        }
        
    def is_path_excluded(self, path: str) -> bool:
        """Check if a path should be excluded from scanning"""
        return any(is_path_under(path, exc) for exc in self.excluded_paths)
        
    def is_extension_excluded(self, filename: str) -> bool:
        """Check if a file extension should be excluded from scanning"""
        ext = os.path.splitext(filename)[1].lower()
        return ext in self.excluded_extensions
        
    def update_exclusions(self, paths: List[str] = None, extensions: List[str] = None):
        """Update exclusion lists"""
        if paths is not None:
            self.excluded_paths = paths
            
        if extensions is not None:
            self.excluded_extensions = [e.lower() for e in extensions]
            
    def update_schedules(self):
        """Reload all schedules from database"""
        # Only reload schedules if this worker has the scheduler running
        # In non-scheduler workers, self.app is None and scheduler isn't started
        if not self.app or not self.scheduler.running:
            logger.debug("Scheduler not running in this worker, skipping update_schedules")
            return

        # Remove all existing scheduled jobs except defaults
        for job in self.scheduler.get_jobs():
            if job.id.startswith('schedule_'):
                self.scheduler.remove_job(job.id)

        # Reload from database
        with self.app.app_context():
            self._load_saved_schedules()
            
    def _check_stuck_scans(self):
        """Check for stuck scans and mark them as crashed"""
        try:
            with self.app.app_context():
                from datetime import datetime, timezone, timedelta
                
                # Consider a scan stuck if no update for 30 minutes
                # This accounts for large files that can take 20+ minutes to scan
                # Use UTC aware datetime for comparison
                current_time = datetime.now(timezone.utc)
                stuck_threshold = current_time - timedelta(minutes=30)
                
                # Find active scans
                stuck_scans = ScanState.query.filter(
                    ScanState.is_active == True,
                    ScanState.phase.notin_(TERMINAL_SCAN_PHASES)
                ).all()
                
                # Backstop for last-chunk-finalizes: if the winning chunk died
                # between chunk-complete and finalize, finish the scan here.
                for scan in stuck_scans:
                    if scan.phase == SCAN_PHASES['SCANNING']:
                        try:
                            if maybe_finalize_scan(scan.scan_id):
                                logger.info(f"Sweeper finalized completed scan {scan.scan_id}")
                        except Exception as e:
                            logger.error(f"Sweeper finalize check failed for {scan.scan_id}: {e}")
                            db.session.rollback()

                stuck_scans = ScanState.query.filter(
                    ScanState.is_active == True,
                    ScanState.phase.notin_(TERMINAL_SCAN_PHASES)
                ).all()

                scans_to_mark = []
                for scan in stuck_scans:
                    # Ensure we have timezone-aware datetimes for comparison
                    # If the database stores naive datetimes, assume they're in UTC
                    last_update = scan.last_update
                    if last_update and last_update.tzinfo is None:
                        last_update = last_update.replace(tzinfo=timezone.utc)

                    start_time = scan.start_time
                    if start_time and start_time.tzinfo is None:
                        start_time = start_time.replace(tzinfo=timezone.utc)

                    # Secondary check: if scan has a Celery task, verify task is still alive.
                    # A lost/crashed Celery task with a stale last_update is a definitive indicator.
                    celery_task_gone = False
                    if scan.celery_task_id:
                        try:
                            from pixelprobe.utils.celery_utils import check_celery_available, safe_check_task_state
                            from flask import current_app
                            if check_celery_available():
                                task_state = safe_check_task_state(scan.celery_task_id, current_app.celery)
                                if task_state in ['SUCCESS', 'FAILURE', 'REVOKED'] or task_state is None:
                                    celery_task_gone = True
                        except Exception:
                            pass  # Celery check failed, rely on time-based detection

                    # Check if scan has been running for more than 30 minutes without update
                    if last_update and last_update < stuck_threshold:
                        logger.warning(f"Marking stuck scan {scan.scan_id} as crashed - no update since {last_update}")
                        scan.is_active = False
                        scan.phase = 'crashed'
                        scan.error_message = f"Scan appears stuck - no activity for over 30 minutes (last update: {last_update})"
                        scans_to_mark.append(scan)
                    elif celery_task_gone and last_update and last_update < (current_time - timedelta(minutes=5)):
                        # Celery task is gone AND no update for 5+ minutes -- crashed
                        logger.warning(f"Marking scan {scan.scan_id} as crashed - Celery task gone and no update since {last_update}")
                        scan.is_active = False
                        scan.phase = 'crashed'
                        scan.error_message = f"Celery task lost and no progress since {last_update}"
                        scans_to_mark.append(scan)
                    elif not last_update and start_time and start_time < stuck_threshold:
                        # No last_update field but scan started over 30 minutes ago
                        logger.warning(f"Marking stuck scan {scan.scan_id} as crashed - started at {start_time} with no updates")
                        scan.is_active = False
                        scan.phase = 'crashed'
                        scan.error_message = f"Scan appears stuck - no activity tracking since start at {start_time}"
                        scans_to_mark.append(scan)
                
                if scans_to_mark:
                    db.session.commit()
                    logger.info(f"Marked {len(scans_to_mark)} stuck scans as crashed")

                # Sweeper: reclaim files left in 'scanning' by a dead worker. A
                # chunk claims files as 'scanning' before processing; if its
                # worker dies, those rows are never re-selected (retry only picks
                # 'pending') and the scan reports complete with them unscanned.
                #
                # Only sweep when there were NO active scans this pass (stuck_scans
                # holds every active non-terminal scan). This deliberately skips a
                # pass that just marked a scan crashed: a slow-but-live chunk of a
                # falsely-flagged scan may still own 'scanning' rows, so it gets
                # reclaimed on the next pass (5 min later) once it is truly idle.
                if not stuck_scans:
                    reclaimed = ScanResult.reclaim_scanning()
                    if reclaimed:
                        db.session.commit()
                        logger.warning(
                            f"Reclaimed {reclaimed} orphaned 'scanning' files to "
                            f"'pending' (no active scan in progress)"
                        )

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error checking for stuck scans: {e}")
    
    @staticmethod
    def send_healthcheck_completion(scan_report_id: int, app=None):
        """Send healthcheck completion ping for a scan report

        This method should be called after a scan completes to send success/failure pings.
        It's static so it can be called from scan services without needing a scheduler instance.

        Args:
            scan_report_id: The ID of the completed scan report
            app: Flask app instance (optional, uses current_app if not provided)
        """
        try:
            from flask import current_app
            if app is None:
                app = current_app._get_current_object()

            with app.app_context():
                # Get the scan report
                scan_report = db.session.get(ScanReport, scan_report_id)
                if not scan_report:
                    logger.warning(f"Scan report {scan_report_id} not found for healthcheck ping")
                    return

                # Try to find associated schedule by matching the scan source
                # Scan reports from scheduled scans have scan_id like 'scheduled_1_20251126_000000'
                # (format: scheduled_{schedule_id}_{timestamp})
                schedule_id = None
                if scan_report.scan_id and scan_report.scan_id.startswith('scheduled_'):
                    try:
                        # Split on '_' and get the second element (index 1) which is the schedule ID
                        # This works for both old format 'scheduled_1' and new 'scheduled_1_20251126_000000'
                        parts = scan_report.scan_id.split('_')
                        if len(parts) >= 2:
                            schedule_id = int(parts[1])
                    except (IndexError, ValueError):
                        logger.debug(f"Could not extract schedule ID from scan_id: {scan_report.scan_id}")

                if not schedule_id:
                    logger.debug(f"Scan report {scan_report_id} is not from a scheduled scan, skipping healthcheck ping")
                    return

                # Get healthcheck configuration for this schedule
                healthcheck_config = HealthcheckConfig.query.filter_by(schedule_id=schedule_id).first()
                if not healthcheck_config or not healthcheck_config.is_active:
                    logger.debug(f"No active healthcheck config for schedule {schedule_id}")
                    return

                # Determine if scan was successful
                is_success = scan_report.status == 'completed'

                # Send appropriate ping
                healthcheck_service = HealthcheckService()

                if is_success and healthcheck_config.send_success_ping:
                    # Prepare report data if configured
                    report_data = None
                    if healthcheck_config.include_report_data:
                        report_data = scan_report.to_dict()

                    success = healthcheck_service.ping_success(
                        healthcheck_config.healthcheck_url,
                        report_data=report_data
                    )

                    healthcheck_config.last_ping_status = 'success' if success else 'failure'
                    healthcheck_config.last_ping_time = datetime.now(timezone.utc)

                    logger.info(f"Healthcheck success ping sent for schedule {schedule_id}: {'success' if success else 'failure'}")

                elif not is_success and healthcheck_config.send_failure_ping:
                    error_message = scan_report.error_message or f"Scan status: {scan_report.status}"

                    success = healthcheck_service.ping_fail(
                        healthcheck_config.healthcheck_url,
                        error_message=error_message
                    )

                    healthcheck_config.last_ping_status = 'failure' if success else 'error'
                    healthcheck_config.last_ping_time = datetime.now(timezone.utc)

                    logger.info(f"Healthcheck failure ping sent for schedule {schedule_id}: {'success' if success else 'failure'}")

                db.session.commit()

        except Exception as e:
            logger.error(f"Error sending healthcheck completion ping: {e}")

    def _run_log_cleanup(self):
        """Run log retention cleanup"""
        try:
            with self.app.app_context():
                from pixelprobe.services.maintenance_service import MaintenanceService
                MaintenanceService.cleanup_old_logs()
        except Exception as e:
            logger.error(f"Failed to run log retention cleanup: {e}")

    def _run_retention_cleanup(self):
        """Enqueue the daily data-retention cleanup to a Celery worker.

        Runs from the single-leader scheduler instead of Celery beat (which was
        configured but never launched), so it cannot fire on more than one
        process even if workers are scaled.
        """
        try:
            from pixelprobe.tasks import run_retention_cleanup
            run_retention_cleanup.delay()
            logger.info("Enqueued daily data retention cleanup task")
        except Exception as e:
            logger.error(f"Failed to enqueue data retention cleanup: {e}")

    def shutdown(self):
        """Shutdown the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown()
import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from models import db, ScanSchedule, ScanResult, ScanState, HealthcheckConfig, ScanReport
from sqlalchemy import text
import threading
import requests
from pixelprobe.services.healthcheck_service import HealthcheckService

logger = logging.getLogger(__name__)

class MediaScheduler:
    def __init__(self, app=None):
        self.scheduler = BackgroundScheduler()
        self.app = app
        self.scan_lock = threading.Lock()
        self.cleanup_lock = threading.Lock()
        self.excluded_paths = []
        self.excluded_extensions = []

        # Load exclusions from environment
        self._load_exclusions()

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
            replace_existing=True
        )
        
    def _add_interval_job(self, job_id: str, func, unit: str, value: int):
        """Add an interval-based job"""
        kwargs = {unit: value}
        trigger = IntervalTrigger(**kwargs)
        
        self.scheduler.add_job(
            func,
            trigger,
            id=job_id,
            replace_existing=True
        )
        
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
            
            # Deduplicate schedules by cron_expression and scan_paths
            seen_schedules = {}
            for schedule in schedules:
                key = f"{schedule.cron_expression}:{schedule.scan_paths}:{schedule.scan_type}"
                
                if key in seen_schedules:
                    # Deactivate duplicate schedule
                    logger.warning(f"Deactivating duplicate schedule {schedule.id}: {schedule.name}")
                    schedule.is_active = False
                    db.session.commit()
                    continue
                    
                seen_schedules[key] = schedule
                self._activate_schedule(schedule)
        except Exception as e:
            logger.error(f"Failed to load saved schedules: {e}")
            
    def _activate_schedule(self, schedule: ScanSchedule):
        """Activate a scan schedule"""
        try:
            job_id = f"schedule_{schedule.id}"
            schedule_id = schedule.id  # Store the ID, not the object

            # Create job function with schedule ID (not the object to avoid detached instance)
            def job_func():
                self._run_scheduled_scan(schedule_id)

            # For interval-based schedules, calculate next_run based on last_run if available
            # This prevents the schedule from resetting on app restart
            next_run_time = None
            if schedule.cron_expression.startswith('interval:'):
                # Parse interval format: interval:unit:value
                parts = schedule.cron_expression.split(':')
                if len(parts) == 3:
                    unit = parts[1]
                    value = int(parts[2])

                    # If we have a last_run time and it's in the past, calculate next run from it
                    if schedule.last_run:
                        from datetime import timedelta
                        interval_kwargs = {unit: value}

                        # Ensure last_run is timezone-aware
                        last_run = schedule.last_run
                        if last_run.tzinfo is None:
                            last_run = last_run.replace(tzinfo=timezone.utc)

                        next_run_time = last_run + timedelta(**interval_kwargs)

                        # If calculated next_run is in the past, use current next_run or now + interval
                        if next_run_time < datetime.now(timezone.utc):
                            if schedule.next_run:
                                # Ensure next_run is timezone-aware for comparison
                                stored_next_run = schedule.next_run
                                if stored_next_run.tzinfo is None:
                                    stored_next_run = stored_next_run.replace(tzinfo=timezone.utc)

                                if stored_next_run > datetime.now(timezone.utc):
                                    next_run_time = stored_next_run
                                else:
                                    next_run_time = datetime.now(timezone.utc) + timedelta(**interval_kwargs)
                            else:
                                next_run_time = datetime.now(timezone.utc) + timedelta(**interval_kwargs)

                    self._add_interval_job(job_id, job_func, unit, value)
                else:
                    raise ValueError(f"Invalid interval format: {schedule.cron_expression}")
            else:
                # Standard cron format
                self._add_cron_job(job_id, job_func, schedule.cron_expression)

            # Update next run time only if we don't have a preserved value
            jobs = self.scheduler.get_jobs()
            for job in jobs:
                if job.id == job_id:
                    # For interval schedules, if we calculated a next_run_time, modify the job
                    if next_run_time:
                        job.modify(next_run_time=next_run_time)
                        schedule.next_run = next_run_time
                    else:
                        # For cron schedules or new interval schedules, use APScheduler's calculation
                        schedule.next_run = job.next_run_time
                    db.session.commit()
                    break

            logger.info(f"Activated schedule: {schedule.name} (next run: {schedule.next_run})")
        except Exception as e:
            logger.error(f"Failed to activate schedule {schedule.name}: {e}")
            
    def _run_periodic_scan(self):
        """Run periodic scan on configured paths via HTTP self-call"""
        if not self.scan_lock.acquire(blocking=False):
            logger.warning("Periodic scan already in progress, skipping")
            return
            
        try:
            with self.app.app_context():
                # Check if ANY scan is already running before proceeding
                scan_state = ScanState.get_or_create()
                if scan_state.is_active and scan_state.phase not in ['idle', 'completed', 'error', 'crashed', 'cancelled']:
                    logger.warning(f"Periodic scan skipped - another scan is already running (phase: {scan_state.phase})")
                    return

                # Read SCAN_PATHS from database (synced from env on main app startup)
                from models import ScanConfiguration
                scan_configs = ScanConfiguration.query.filter_by(is_active=True).all()
                scan_paths = [config.path for config in scan_configs if config.path]

                if not scan_paths:
                    # Fallback to environment variable if DB is empty
                    scan_paths_env = os.environ.get('SCAN_PATHS', '')
                    scan_paths = [p.strip() for p in scan_paths_env.split(',') if p.strip()]

                logger.info(f"Starting periodic scan of paths: {scan_paths}")
                
                # Filter out excluded paths
                filtered_paths = []
                for path in scan_paths:
                    path = path.strip()
                    if not any(path.startswith(exc) for exc in self.excluded_paths):
                        filtered_paths.append(path)
                        
                if not filtered_paths:
                    logger.warning("No paths to scan after exclusions")
                    return
                    
                # Use HTTP self-call to trigger scan
                base_url = self._get_api_base_url()
                
                # Add internal request header
                headers = {
                    'X-Internal-Request': 'scheduler',
                    'Content-Type': 'application/json'
                }
                
                try:
                    # Run scan with deep check to detect changes
                    payload = {
                        'scan_type': 'full',
                        'directories': filtered_paths,
                        'force_rescan': False,
                        'source': 'scheduled_periodic'
                    }
                    response = requests.post(f'{base_url}/api/scan',
                                          json=payload,
                                          headers=headers,
                                          timeout=30)
                    
                    if response.status_code == 200:
                        logger.info("Periodic scan started successfully")
                    elif response.status_code == 409:
                        logger.warning("Periodic scan skipped - another scan is already running")
                    else:
                        logger.error(f"Periodic scan API call failed: {response.status_code} - {response.text}")
                        
                except requests.exceptions.RequestException as e:
                    logger.error(f"Failed to call API for periodic scan: {e}")
                
        except Exception as e:
            logger.error(f"Failed to run periodic scan: {e}")
        finally:
            self.scan_lock.release()
            
    def _run_scheduled_scan(self, schedule_id: int):
        """Run a scheduled scan via HTTP self-call to avoid Flask context issues"""
        if not self.scan_lock.acquire(blocking=False):
            logger.warning(f"Scheduled scan {schedule_id} already in progress, skipping")
            return

        try:
            with self.app.app_context():
                # Check if ANY scan is already running before proceeding
                scan_state = ScanState.get_or_create()
                if scan_state.is_active and scan_state.phase not in ['idle', 'completed', 'error', 'crashed', 'cancelled']:
                    logger.warning(f"Scheduled scan {schedule_id} skipped - another scan is already running (phase: {scan_state.phase})")
                    return

                schedule = ScanSchedule.query.get(schedule_id)
                if not schedule or not schedule.is_active:
                    return

                # Cache schedule attributes BEFORE any commits
                # SQLAlchemy expires objects after commit(), and lazy-loading can fail
                # in the celery worker context, returning None for these attributes
                cached_scan_type = getattr(schedule, 'scan_type', 'normal')
                cached_schedule_name = schedule.name

                # Check for healthcheck configuration and send start ping
                healthcheck_config = HealthcheckConfig.query.filter_by(schedule_id=schedule_id).first()
                if healthcheck_config and healthcheck_config.is_active and healthcheck_config.send_start_ping:
                    try:
                        healthcheck_service = HealthcheckService()
                        success = healthcheck_service.ping_start(healthcheck_config.healthcheck_url)

                        # Update ping status
                        healthcheck_config.last_ping_status = 'success' if success else 'failure'
                        healthcheck_config.last_ping_time = datetime.now(timezone.utc)
                        db.session.commit()

                        logger.info(f"Healthcheck start ping sent for schedule {schedule_id}: {'success' if success else 'failure'}")
                    except Exception as e:
                        logger.error(f"Failed to send healthcheck start ping for schedule {schedule_id}: {e}")
                    
                # Update last run time
                schedule.last_run = datetime.now(timezone.utc)
                
                # Update next run time from APScheduler
                job_id = f"schedule_{schedule_id}"
                jobs = self.scheduler.get_jobs()
                for job in jobs:
                    if job.id == job_id:
                        schedule.next_run = job.next_run_time
                        break
                
                db.session.commit()

                # Read SCAN_PATHS from database (synced from env on main app startup)
                # This allows celery-worker to access paths without needing env var
                from models import ScanConfiguration
                scan_configs = ScanConfiguration.query.filter_by(is_active=True).all()
                scan_paths = [config.path for config in scan_configs if config.path]

                if not scan_paths:
                    # Fallback to environment variable if DB is empty (for backwards compat)
                    scan_paths_env = os.environ.get('SCAN_PATHS', '')
                    scan_paths = [p.strip() for p in scan_paths_env.split(',') if p.strip()]

                if not scan_paths:
                    logger.error(f"Scheduled scan {schedule_id}: No scan paths configured in database or SCAN_PATHS env var!")
                    return

                # Use cached scan_type
                scan_type = cached_scan_type

                logger.info(f"Running scheduled scan '{cached_schedule_name}' (type: {scan_type}) on paths: {scan_paths}")

                # Filter out excluded paths
                filtered_paths = []
                for path in scan_paths:
                    path = path.strip()
                    if not any(path.startswith(exc) for exc in self.excluded_paths):
                        filtered_paths.append(path)

                # Validate we have paths to scan after filtering
                if not filtered_paths:
                    logger.error(f"Scheduled scan {schedule_id} has no valid paths after filtering. "
                                 f"SCAN_PATHS={scan_paths}, excluded_paths={self.excluded_paths}")
                    return

                if filtered_paths:
                    # Use HTTP self-calls to trigger scans, avoiding Flask context issues
                    base_url = self._get_api_base_url()
                    
                    # Add internal request header for identification
                    headers = {
                        'X-Internal-Request': 'scheduler',
                        'Content-Type': 'application/json'
                    }
                    
                    try:
                        if scan_type == 'orphan':
                            # Run orphan cleanup with longer timeout since it can take time
                            response = requests.post(f'{base_url}/api/cleanup-orphaned',
                                                    json={'schedule_id': schedule_id},
                                                    headers=headers,
                                                    timeout=60)
                        elif scan_type == 'file_changes':
                            # Run file changes scan with longer timeout
                            response = requests.post(f'{base_url}/api/file-changes',
                                                    json={'schedule_id': schedule_id},
                                                    headers=headers,
                                                    timeout=60)
                        else:
                            # Default to normal scan with proper payload
                            # Use timestamp in scan_id to make each scheduled run unique
                            # This prevents false "already completed" detection between runs
                            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
                            payload = {
                                'scan_type': 'full',
                                'directories': filtered_paths,
                                'force_rescan': schedule.force_rescan if hasattr(schedule, 'force_rescan') else False,
                                'source': f'scheduled_{schedule_id}_{timestamp}'
                            }
                            response = requests.post(f'{base_url}/api/scan', 
                                                    json=payload,
                                                    headers=headers,
                                                    timeout=60)
                        
                        if response.status_code == 200:
                            logger.info(f"Scheduled scan {schedule_id} started successfully")
                        elif response.status_code == 409:
                            logger.warning(f"Scheduled scan {schedule_id} skipped - another scan is already running")
                        else:
                            logger.error(f"Scheduled scan {schedule_id} API call failed: {response.status_code} - {response.text}")
                            
                    except requests.exceptions.RequestException as e:
                        logger.error(f"Failed to call API for scheduled scan {schedule_id}: {e}")
                    
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

                base_url = self._get_api_base_url()
                
                # Add internal request header
                headers = {
                    'X-Internal-Request': 'scheduler',
                    'Content-Type': 'application/json'
                }
                
                try:
                    response = requests.post(f'{base_url}/api/cleanup-orphaned', 
                                          headers=headers,
                                          timeout=60)
                    
                    if response.status_code == 200:
                        logger.info("Cleanup task started successfully")
                    elif response.status_code == 409:
                        logger.warning("Cleanup skipped - another scan/cleanup is already running")
                    else:
                        logger.error(f"Cleanup API call failed: {response.status_code} - {response.text}")
                        
                except requests.exceptions.RequestException as e:
                    logger.error(f"Failed to call API for cleanup: {e}")
                
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
        schedule = ScanSchedule.query.get(schedule_id)
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
        schedule = ScanSchedule.query.get(schedule_id)
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
        return any(path.startswith(exc) for exc in self.excluded_paths)
        
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
                    ScanState.phase.notin_(['idle', 'completed', 'error', 'crashed', 'cancelled'])
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
                    
                    # Check if scan has been running for more than 30 minutes without update
                    if last_update and last_update < stuck_threshold:
                        logger.warning(f"Marking stuck scan {scan.scan_id} as crashed - no update since {last_update}")
                        scan.is_active = False
                        scan.phase = 'crashed'
                        scan.error_message = f"Scan appears stuck - no activity for over 30 minutes (last update: {last_update})"
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
                    
        except Exception as e:
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
                scan_report = ScanReport.query.get(scan_report_id)
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

    def shutdown(self):
        """Shutdown the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown()
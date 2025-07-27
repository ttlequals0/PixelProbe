import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from models import db, ScanSchedule, ScanResult
from sqlalchemy import text
import threading

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
        
    def init_app(self, app):
        self.app = app
        self.scheduler.start()
        
        # Schedule default tasks from environment variables
        self._schedule_default_tasks()
        
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
            schedules = ScanSchedule.query.filter_by(is_active=True).all()
            for schedule in schedules:
                self._activate_schedule(schedule)
        except Exception as e:
            logger.error(f"Failed to load saved schedules: {e}")
            
    def _activate_schedule(self, schedule: ScanSchedule):
        """Activate a scan schedule"""
        try:
            job_id = f"schedule_{schedule.id}"
            
            # Create job function with schedule context
            def job_func():
                self._run_scheduled_scan(schedule.id)
                
            # Check if it's an interval or cron format
            if schedule.cron_expression.startswith('interval:'):
                # Parse interval format: interval:unit:value
                parts = schedule.cron_expression.split(':')
                if len(parts) == 3:
                    unit = parts[1]
                    value = int(parts[2])
                    self._add_interval_job(job_id, job_func, unit, value)
                else:
                    raise ValueError(f"Invalid interval format: {schedule.cron_expression}")
            else:
                # Standard cron format
                self._add_cron_job(job_id, job_func, schedule.cron_expression)
            
            # Update next run time
            jobs = self.scheduler.get_jobs()
            for job in jobs:
                if job.id == job_id:
                    schedule.next_run = job.next_run_time
                    db.session.commit()
                    break
                    
            logger.info(f"Activated schedule: {schedule.name}")
        except Exception as e:
            logger.error(f"Failed to activate schedule {schedule.name}: {e}")
            
    def _run_periodic_scan(self):
        """Run periodic scan on configured paths"""
        if not self.scan_lock.acquire(blocking=False):
            logger.warning("Periodic scan already in progress, skipping")
            return
            
        try:
            with self.app.app_context():
                scan_paths = os.environ.get('SCAN_PATHS', '/media').split(',')
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
                    
                # Run scan with deep check to detect changes
                import requests
                requests.post('http://localhost:5000/api/scan-all', 
                            json={'deep_scan': True, 'paths': filtered_paths},
                            timeout=5)
                
        finally:
            self.scan_lock.release()
            
    def _run_scheduled_scan(self, schedule_id: int):
        """Run a scheduled scan"""
        if not self.scan_lock.acquire(blocking=False):
            logger.warning(f"Scheduled scan {schedule_id} already in progress, skipping")
            return
            
        try:
            with self.app.app_context():
                schedule = ScanSchedule.query.get(schedule_id)
                if not schedule or not schedule.is_active:
                    return
                    
                # Update last run time
                schedule.last_run = datetime.utcnow()
                db.session.commit()
                
                # Parse scan paths
                scan_paths = json.loads(schedule.scan_paths) if schedule.scan_paths else []
                if not scan_paths:
                    scan_paths = os.environ.get('SCAN_PATHS', '/media').split(',')
                    
                logger.info(f"Running scheduled scan '{schedule.name}' (type: {getattr(schedule, 'scan_type', 'normal')}) on paths: {scan_paths}")
                
                # Get scan type (default to 'normal' for backward compatibility)
                scan_type = getattr(schedule, 'scan_type', 'normal')
                
                # Filter out excluded paths
                filtered_paths = []
                for path in scan_paths:
                    path = path.strip()
                    if not any(path.startswith(exc) for exc in self.excluded_paths):
                        filtered_paths.append(path)
                        
                if filtered_paths:
                    # Trigger scan through API endpoint based on scan type
                    import requests
                    
                    if scan_type == 'orphan':
                        # Run orphan cleanup
                        requests.post('http://localhost:5000/api/cleanup-orphaned', timeout=5)
                    elif scan_type == 'file_changes':
                        # Run file changes scan
                        requests.post('http://localhost:5000/api/file-changes', timeout=5)
                    else:
                        # Default to normal scan
                        requests.post('http://localhost:5000/api/scan-all', 
                                    json={'deep_scan': True, 'paths': filtered_paths},
                                    timeout=5)
                    
        except Exception as e:
            logger.error(f"Failed to run scheduled scan {schedule_id}: {e}")
        finally:
            self.scan_lock.release()
            
    def _run_cleanup(self):
        """Run cleanup of orphaned records"""
        if not self.cleanup_lock.acquire(blocking=False):
            logger.warning("Cleanup already in progress, skipping")
            return
            
        try:
            with self.app.app_context():
                logger.info("Starting scheduled cleanup of orphaned records")
                
                import requests
                requests.post('http://localhost:5000/api/cleanup-orphaned', timeout=5)
                
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
        except:
            pass
            
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
        except:
            pass
            
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
        # Remove all existing scheduled jobs except defaults
        for job in self.scheduler.get_jobs():
            if job.id.startswith('schedule_'):
                self.scheduler.remove_job(job.id)
        
        # Reload from database
        with self.app.app_context():
            self._load_saved_schedules()
            
    def shutdown(self):
        """Shutdown the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown()
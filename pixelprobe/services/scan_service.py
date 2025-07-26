"""
Scan service for handling media scanning operations
"""

import os
import json
import threading
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

from flask import current_app
from media_checker import PixelProbe, load_exclusions
from models import db, ScanResult, ScanState, ScanReport
from utils import ProgressTracker

logger = logging.getLogger(__name__)

class ScanService:
    """Service for managing scan operations"""
    
    def __init__(self, database_uri: str):
        self.database_uri = database_uri
        self.current_scan_thread: Optional[threading.Thread] = None
        self.scan_cancelled = False
        self.scan_progress = {
            'current': 0,
            'total': 0,
            'file': '',
            'status': 'idle'
        }
        self.progress_lock = threading.Lock()
        
    def is_scan_running(self) -> bool:
        """Check if a scan is currently running"""
        return self.current_scan_thread is not None and self.current_scan_thread.is_alive()
    
    def get_scan_progress(self) -> Dict:
        """Get current scan progress"""
        with self.progress_lock:
            return self.scan_progress.copy()
    
    def update_progress(self, current: int, total: int, file_path: str, status: str):
        """Update scan progress"""
        with self.progress_lock:
            self.scan_progress.update({
                'current': current,
                'total': total,
                'file': file_path,
                'status': status
            })
    
    def scan_single_file(self, file_path: str, force_rescan: bool = False) -> Dict:
        """Scan a single file"""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        if self.is_scan_running():
            raise RuntimeError("Another scan is already in progress")
        
        # Initialize progress
        self.update_progress(0, 1, file_path, 'scanning')
        self.scan_cancelled = False
        
        # Capture Flask app context for the thread
        app = current_app._get_current_object()
        
        # Create scan thread
        def run_scan():
            # Set up Flask app context for the thread
            with app.app_context():
                try:
                    excluded_paths, excluded_extensions = load_exclusions()
                    checker = PixelProbe(
                        database_path=self.database_uri,
                        excluded_paths=excluded_paths,
                        excluded_extensions=excluded_extensions
                    )
                    result = checker.scan_file(file_path, force_rescan=force_rescan)
                    self.update_progress(1, 1, file_path, 'completed')
                    return result
                except Exception as e:
                    logger.error(f"Error scanning file: {e}")
                    self.update_progress(1, 1, file_path, 'error')
                    raise
        
        self.current_scan_thread = threading.Thread(target=run_scan)
        self.current_scan_thread.start()
        
        return {'status': 'started', 'message': 'Scan started', 'file_path': file_path}
    
    def scan_directories(self, directories: List[str], force_rescan: bool = False, 
                        num_workers: int = 1) -> Dict:
        """Scan multiple directories"""
        if self.is_scan_running():
            raise RuntimeError("Another scan is already in progress")
        
        # Validate directories
        valid_dirs = [d for d in directories if os.path.exists(d)]
        if not valid_dirs:
            raise ValueError("No valid directories provided")
        
        # Initialize progress
        self.update_progress(0, 0, '', 'initializing')
        self.scan_cancelled = False
        
        # Save scan state and capture ID before threading
        scan_state = ScanState.get_or_create()
        scan_state.start_scan(valid_dirs, force_rescan)
        db.session.commit()
        
        # Capture scan ID while the object is still bound to the session
        scan_state_id = scan_state.id
        
        # Capture Flask app context for the thread
        app = current_app._get_current_object()
        
        # Create scan thread
        def run_scan():
            # Set up Flask app context for the thread
            with app.app_context():
                try:
                    # Get fresh ScanState object in worker thread to avoid detached instance
                    scan_state = db.session.get(ScanState, scan_state_id)
                    if not scan_state:
                        logger.error(f"Could not find scan state with ID {scan_state_id}")
                        return
                    
                    excluded_paths, excluded_extensions = load_exclusions()
                    checker = PixelProbe(
                        database_path=self.database_uri,
                        excluded_paths=excluded_paths,
                        excluded_extensions=excluded_extensions
                    )
                    
                    # Create progress tracker for scan operations
                    progress_tracker = ProgressTracker('scan')
                    
                    # Phase 1: Discovery - Find only new files
                    self.update_progress(0, 0, '', 'discovering')
                    scan_state.update_progress(0, 0, phase='discovering')
                    scan_state.progress_message = 'Phase 1 of 3: Discovering media files...'
                    db.session.commit()
                    
                    if self.scan_cancelled:
                        self._handle_scan_cancellation(scan_state)
                        return
                    
                    # Get existing files to skip during discovery (optimized for large databases)
                    from models import ScanResult
                    logger.info("Starting file discovery with on-demand database checking...")
                    
                    # Get all existing file paths from database for faster lookup
                    logger.info("Loading existing file paths from database...")
                    existing_file_paths = set()
                    batch_size = 50000
                    offset = 0
                    
                    while True:
                        batch = db.session.query(ScanResult.file_path).offset(offset).limit(batch_size).all()
                        if not batch:
                            break
                        existing_file_paths.update(result.file_path for result in batch)
                        offset += batch_size
                        if offset % 100000 == 0:
                            logger.info(f"Loaded {offset} existing file paths...")
                    
                    logger.info(f"Loaded {len(existing_file_paths)} existing file paths from database")
                    existing_files = existing_file_paths
                    
                    # Define progress callback for discovery
                    def discovery_progress(files_checked, files_discovered):
                        self.update_progress(files_checked, files_checked, '', 'discovering')
                        scan_state.update_progress(files_checked, files_checked, phase='discovering', current_file='')
                        scan_state.discovery_count = files_discovered
                        db.session.commit()
                    
                    # Discover only new files (not already in database)
                    all_files = checker.discover_media_files(valid_dirs, existing_files=existing_files, progress_callback=discovery_progress)
                    logger.info(f"File discovery completed. Found {len(all_files)} files to process")
                    new_files_count = len(all_files)
                    
                    if self.scan_cancelled:
                        self._handle_scan_cancellation(scan_state)
                        return
                    
                    # Phase 2: Adding - Add new files to database with basic info
                    if new_files_count > 0:
                        self.update_progress(0, new_files_count, '', 'adding')
                        scan_state.update_progress(0, new_files_count, phase='adding')
                        scan_state.progress_message = f'Phase 2 of 3: Adding {new_files_count} new files to database...'
                        db.session.commit()
                        
                        # Add new files to database with basic file info (no corruption check yet)
                        added_count = 0
                        duplicate_count = 0
                        
                        for i, file_path in enumerate(all_files):
                            if self.scan_cancelled:
                                self._handle_scan_cancellation(scan_state)
                                return
                            
                            # Safety check: if too many duplicates, something is wrong with discovery
                            if i > 1000 and duplicate_count > (i * 0.95):  # More than 95% duplicates
                                logger.error(f"Too many duplicate files detected ({duplicate_count}/{i}). Discovery phase may have failed.")
                                logger.error("Aborting add phase to prevent infinite loop.")
                                break
                                
                            was_added = self._add_file_to_db(file_path)
                            if was_added:
                                added_count += 1
                            else:
                                duplicate_count += 1
                            
                            self.update_progress(i + 1, new_files_count, file_path, 'adding')
                            scan_state.update_progress(i + 1, new_files_count, current_file=file_path)
                            
                            if (i + 1) % 100 == 0:  # Commit in batches for performance
                                db.session.commit()
                                logger.info(f"Added {added_count} new files out of {i + 1} processed ({duplicate_count} duplicates)")
                                
                                # Early warning if too many duplicates
                                if duplicate_count > (i * 0.8):  # More than 80% duplicates
                                    logger.warning(f"High duplicate rate detected: {duplicate_count}/{i} files already existed")
                        
                        db.session.commit()
                        logger.info(f"Add phase completed. Added {added_count} new files out of {new_files_count} discovered")
                    
                    # Phase 3: Scanning - Check integrity of files that need scanning
                    if force_rescan:
                        # If force_rescan, check ALL files in the directories
                        from models import ScanResult
                        files_to_scan = [
                            result.file_path for result in db.session.query(ScanResult).filter(
                                db.or_(*[ScanResult.file_path.like(f"{d}%") for d in valid_dirs])
                            ).all()
                        ]
                    else:
                        # Only scan new files and files that haven't been scanned
                        files_to_scan = all_files  # Just the new files discovered
                    
                    total_scan_files = len(files_to_scan)
                    logger.info(f"Starting scan phase: {total_scan_files} files to scan")
                    
                    # Special case: if no files to scan, complete immediately
                    if total_scan_files == 0:
                        logger.info("No files to scan - completing scan immediately")
                        self.update_progress(0, 0, '', 'completed')
                        
                        # Complete scan using thread-safe database update
                        # Use the scan_state_id we captured before threading
                        from sqlalchemy import text
                        db.session.execute(
                            text("UPDATE scan_state SET phase = 'completed', is_active = false, end_time = :end_time WHERE id = :id"),
                            {'end_time': datetime.now(timezone.utc), 'id': scan_state_id}
                        )
                        db.session.commit()
                        
                        # Create scan report even for empty scans
                        completed_scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                        if completed_scan_state:
                            self._create_scan_report(completed_scan_state, scan_type='full_scan' if not force_rescan else 'rescan')
                        
                        logger.info(f"Scan {scan_state_id} completed immediately (no files to process)")
                        return {'message': 'Scan completed - no files to process', 'total_files': 0}
                    
                    # Update both service and database state for actual scanning
                    self.update_progress(0, total_scan_files, '', 'scanning')
                    scan_state.update_progress(0, total_scan_files, phase='scanning', current_file='')
                    scan_state.progress_message = f'Phase 3 of 3: Scanning {total_scan_files} files for corruption...'
                    
                    # Explicit commit to ensure database state is updated
                    db.session.commit()
                    logger.info(f"Scan state transitioned to 'scanning' phase "
                               f"with {total_scan_files} files")
                    
                    if num_workers > 1:
                        self._parallel_scan(checker, files_to_scan, force_rescan, num_workers, scan_state, scan_state_id)
                    else:
                        self._sequential_scan(checker, files_to_scan, force_rescan, scan_state, scan_state_id)
                        
                except Exception as e:
                    logger.error(f"Error during scan: {e}")
                    self.update_progress(0, 0, '', 'error')
                    scan_state.error_scan(str(e))
                    db.session.commit()
                    raise
        
        self.current_scan_thread = threading.Thread(target=run_scan)
        self.current_scan_thread.start()
        
        return {
            'status': 'started',
            'message': 'Scan started',
            'directories': valid_dirs,
            'force_rescan': force_rescan,
            'num_workers': num_workers
        }
    
    def scan_files(self, file_paths: List[str], force_rescan: bool = False,
                   deep_scan: bool = False, num_workers: int = 1) -> Dict:
        """Scan specific files only"""
        if self.is_scan_running():
            raise RuntimeError("Another scan is already in progress")
        
        # Validate files exist
        valid_files = [f for f in file_paths if os.path.exists(f)]
        if not valid_files:
            raise ValueError("No valid files provided")
        
        logger.info(f"Starting scan of {len(valid_files)} specific files")
        
        # Initialize progress
        self.update_progress(0, 0, '', 'initializing')
        self.scan_cancelled = False
        
        # Save scan state
        scan_state = ScanState.get_or_create()
        scan_state.start_scan(["selected_files"], force_rescan)
        db.session.commit()
        
        # Capture scan ID
        scan_state_id = scan_state.id
        
        # Capture Flask app context for the thread
        app = current_app._get_current_object()
        
        # Create scan thread
        def run_scan():
            with app.app_context():
                try:
                    # Get fresh ScanState object in worker thread
                    scan_state = db.session.get(ScanState, scan_state_id)
                    if not scan_state:
                        logger.error(f"Could not find scan state with ID {scan_state_id}")
                        return
                    
                    excluded_paths, excluded_extensions = load_exclusions()
                    checker = PixelProbe(
                        database_path=self.database_uri,
                        excluded_paths=excluded_paths,
                        excluded_extensions=excluded_extensions
                    )
                    
                    # Skip discovery phase - we already have the files
                    total_files = len(valid_files)
                    logger.info(f"Scanning {total_files} specific files")
                    
                    # Go directly to scanning phase
                    self.update_progress(0, total_files, '', 'scanning')
                    scan_state.update_progress(0, total_files, phase='scanning')
                    scan_state.progress_message = f'Scanning {total_files} selected files...'
                    db.session.commit()
                    
                    if num_workers > 1:
                        self._parallel_scan(checker, valid_files, force_rescan, num_workers, scan_state, scan_state_id)
                    else:
                        self._sequential_scan(checker, valid_files, force_rescan, scan_state, scan_state_id)
                        
                except Exception as e:
                    logger.error(f"Error during file scan: {e}")
                    self.update_progress(0, 0, '', 'error')
                    scan_state.error_scan(str(e))
                    db.session.commit()
                    raise
        
        self.current_scan_thread = threading.Thread(target=run_scan)
        self.current_scan_thread.start()
        
        return {
            'status': 'started',
            'message': f'Scan started for {len(valid_files)} files',
            'files': len(valid_files),
            'force_rescan': force_rescan,
            'deep_scan': deep_scan,
            'num_workers': num_workers
        }
    
    def cancel_scan(self) -> Dict:
        """Cancel the current scan"""
        if not self.is_scan_running():
            raise RuntimeError("No scan is currently running")
        
        self.scan_cancelled = True
        
        # Update scan state
        scan_state = ScanState.get_or_create()
        scan_state.cancel_scan()
        db.session.commit()
        
        return {'message': 'Scan cancellation requested'}
    
    def reset_stuck_scans(self) -> Dict:
        """Reset files stuck in scanning state"""
        stuck_results = ScanResult.query.filter_by(scan_status='scanning').all()
        count = len(stuck_results)
        
        for result in stuck_results:
            result.scan_status = 'pending'
            result.error_message = 'Reset from stuck scanning state'
        
        db.session.commit()
        
        return {'message': f'Reset {count} stuck files', 'count': count}
    
    def _sequential_scan(self, checker: PixelProbe, files: List[str], 
                        force_rescan: bool, scan_state: ScanState, scan_state_id: int):
        """Perform sequential scan of files"""
        total_files = len(files)
        
        # Create progress tracker for scan
        progress_tracker = ProgressTracker('scan')
        
        for i, file_path in enumerate(files):
            if self.scan_cancelled:
                break
            
            self.update_progress(i, total_files, file_path, 'scanning')
            
            try:
                checker.scan_file(file_path, force_rescan=force_rescan)
            except Exception as e:
                logger.error(f"Error scanning file {file_path}: {e}")
            
            # Update scan state progress
            scan_state.update_progress(i + 1, total_files, current_file=file_path)
            
            # Update progress message with current file and ETA
            scan_state.progress_message = progress_tracker.get_progress_message(
                f'Phase 3 of 3: Scanning {total_files} files for corruption',
                i + 1,
                total_files,
                os.path.basename(file_path)
            )
            db.session.commit()
            
            # Log progress every 10 files for UI debugging
            if (i + 1) % 10 == 0:
                logger.info(f"Scan progress: {i + 1}/{total_files} files processed")
        
        # Complete scan
        if self.scan_cancelled:
            self._handle_scan_cancellation(scan_state)
        else:
            self.update_progress(total_files, total_files, '', 'completed')
            
            # Thread-safe completion using direct SQL update
            # Use scan_state_id which is accessible in this closure
            from sqlalchemy import text
            db.session.execute(
                text("UPDATE scan_state SET phase = 'completed', is_active = false, end_time = :end_time WHERE id = :id"),
                {'end_time': datetime.now(timezone.utc), 'id': scan_state_id}
            )
            db.session.commit()
            
            # Create scan report
            # Re-fetch scan state to get updated values
            completed_scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
            if completed_scan_state:
                self._create_scan_report(completed_scan_state, scan_type='full_scan' if not force_rescan else 'rescan')
    
    def _parallel_scan(self, checker: PixelProbe, files: List[str], 
                      force_rescan: bool, num_workers: int, scan_state: ScanState, scan_state_id: int):
        """Perform parallel scan of files"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        total_files = len(files)
        completed = 0
        
        # Create progress tracker for scan
        progress_tracker = ProgressTracker('scan')
        
        def scan_file(file_path):
            if self.scan_cancelled:
                return None
            try:
                return checker.scan_file(file_path, force_rescan=force_rescan)
            except Exception as e:
                logger.error(f"Error scanning file {file_path}: {e}")
                return None
        
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            # Submit all files for scanning
            future_to_file = {executor.submit(scan_file, f): f for f in files}
            
            # Process completed scans
            for future in as_completed(future_to_file):
                if self.scan_cancelled:
                    executor.shutdown(wait=False)
                    break
                
                file_path = future_to_file[future]
                completed += 1
                
                self.update_progress(completed, total_files, file_path, 'scanning')
                
                # Update scan state progress
                scan_state.update_progress(completed, total_files, current_file=file_path)
                
                # Update progress message with current file and ETA
                scan_state.progress_message = progress_tracker.get_progress_message(
                    f'Phase 3 of 3: Scanning {total_files} files for corruption',
                    completed,
                    total_files,
                    os.path.basename(file_path)
                )
                db.session.commit()
                
                # Log progress every 10 files for UI debugging
                if completed % 10 == 0:
                    logger.info(f"Parallel scan progress: {completed}/{total_files} files processed")
        
        # Complete scan
        if self.scan_cancelled:
            self._handle_scan_cancellation(scan_state)
        else:
            self.update_progress(total_files, total_files, '', 'completed')
            
            # Thread-safe completion using direct SQL update
            # Use scan_state_id which is accessible in this closure
            from sqlalchemy import text
            db.session.execute(
                text("UPDATE scan_state SET phase = 'completed', is_active = false, end_time = :end_time WHERE id = :id"),
                {'end_time': datetime.now(timezone.utc), 'id': scan_state_id}
            )
            db.session.commit()
            
            # Create scan report
            # Re-fetch scan state to get updated values
            completed_scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
            if completed_scan_state:
                self._create_scan_report(completed_scan_state, scan_type='full_scan' if not force_rescan else 'rescan')
    
    def _create_scan_report(self, scan_state: ScanState, scan_type: str = 'full_scan'):
        """Create a scan report from the completed scan state"""
        try:
            # Get statistics from the database
            from sqlalchemy import func
            
            # Count files by status
            stats = db.session.query(
                func.count(ScanResult.id).label('total'),
                func.sum(db.case((ScanResult.is_corrupted == True, 1), else_=0)).label('corrupted'),
                func.sum(db.case((ScanResult.has_warnings == True, 1), else_=0)).label('warnings'),
                func.sum(db.case((ScanResult.scan_status == 'error', 1), else_=0)).label('errors'),
                func.sum(db.case((ScanResult.scan_status == 'completed', 1), else_=0)).label('completed')
            ).first()
            
            # Calculate duration - handle both timezone-aware and naive datetimes
            duration = None
            if scan_state.start_time and scan_state.end_time:
                # Ensure both times are timezone-aware for comparison
                start_time = scan_state.start_time
                end_time = scan_state.end_time
                
                # If start_time is naive, make it UTC aware
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)
                
                # If end_time is naive, make it UTC aware
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=timezone.utc)
                
                duration = (end_time - start_time).total_seconds()
            
            # Create scan report
            report = ScanReport(
                scan_type=scan_type,
                start_time=scan_state.start_time,
                end_time=scan_state.end_time,
                duration_seconds=duration,
                directories_scanned=json.dumps(scan_state.directories) if scan_state.directories else None,
                force_rescan=scan_state.force_rescan,
                num_workers=1,  # TODO: Get from scan state
                total_files_discovered=scan_state.estimated_total,
                files_scanned=stats.completed or 0,
                files_added=0,  # TODO: Track new files added
                files_updated=0,  # TODO: Track files updated
                files_corrupted=stats.corrupted or 0,
                files_with_warnings=stats.warnings or 0,
                files_error=stats.errors or 0,
                status='completed' if scan_state.phase == 'completed' else scan_state.phase,
                error_message=scan_state.error_message,
                scan_id=scan_state.scan_id
            )
            
            db.session.add(report)
            db.session.commit()
            
            logger.info(f"Created scan report {report.report_id} for scan {scan_state.scan_id}")
            
        except Exception as e:
            logger.error(f"Failed to create scan report: {e}")
    
    def _handle_scan_cancellation(self, scan_state: ScanState):
        """Handle scan cancellation"""
        self.update_progress(
            self.scan_progress['current'],
            self.scan_progress['total'],
            '',
            'cancelled'
        )
        scan_state.cancel_scan()
        db.session.commit()
    
    def _add_file_to_db(self, file_path: str) -> bool:
        """Add a new file to the database with basic info (no corruption check)
        
        Returns:
            bool: True if file was added, False if it already existed
        """
        import os
        import magic
        import hashlib
        from datetime import datetime
        from models import ScanResult
        
        # Safety check: Discovery phase should have already filtered out existing files
        # but we keep this as a backup for edge cases
        existing = db.session.query(ScanResult).filter_by(file_path=file_path).first()
        if existing:
            return False  # Skip if already exists (should be rare)
        
        try:
            # Get file stats
            stat = os.stat(file_path)
            file_size = stat.st_size
            mod_time = datetime.fromtimestamp(stat.st_mtime)
            
            # Detect MIME type
            mime_type = magic.from_file(file_path, mime=True)
            
            # Calculate MD5 hash for quick duplicate detection
            hasher = hashlib.md5()
            with open(file_path, 'rb') as f:
                # Read in chunks for large files
                for chunk in iter(lambda: f.read(4096), b""):
                    hasher.update(chunk)
            file_hash = hasher.hexdigest()
            
            # Create ScanResult entry with basic info, no corruption check yet
            scan_result = ScanResult(
                file_path=file_path,
                file_size=file_size,
                file_hash=file_hash,
                file_type=mime_type,
                last_modified=mod_time,
                scan_date=datetime.utcnow(),
                scan_status='pending',  # Mark as pending corruption check
                is_corrupted=False,
                marked_as_good=False
            )
            
            db.session.add(scan_result)
            return True
            
        except Exception as e:
            logger.error(f"Failed to add file to database: {file_path} - {e}")
            # Create minimal entry if basic info fails
            scan_result = ScanResult(
                file_path=file_path,
                scan_date=datetime.utcnow(),
                scan_status='error',
                error_message=str(e),
                is_corrupted=False,
                marked_as_good=False
            )
            db.session.add(scan_result)
            return True
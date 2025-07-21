"""
Scan service for handling media scanning operations
"""

import os
import threading
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

from flask import current_app
from media_checker import PixelProbe, load_exclusions
from models import db, ScanResult, ScanState

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
        
        return {'message': 'Scan started', 'file_path': file_path}
    
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
        
        # Save scan state
        scan_state = ScanState.get_or_create()
        scan_state.start_scan(valid_dirs, force_rescan)
        db.session.commit()
        
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
                    
                    # Discovery phase
                    self.update_progress(0, 0, '', 'discovering')
                    
                    if self.scan_cancelled:
                        self._handle_scan_cancellation(scan_state)
                        return
                    
                    # Pass all directories at once for efficient parallel discovery
                    all_files = checker.discover_media_files(valid_dirs)
                    
                    if self.scan_cancelled:
                        self._handle_scan_cancellation(scan_state)
                        return
                    
                    # Scanning phase
                    total_files = len(all_files)
                    self.update_progress(0, total_files, '', 'scanning')
                    
                    # Update database scan state with total files discovered
                    scan_state.update_progress(0, total_files)
                    db.session.commit()
                    
                    if num_workers > 1:
                        self._parallel_scan(checker, all_files, force_rescan, num_workers, scan_state)
                    else:
                        self._sequential_scan(checker, all_files, force_rescan, scan_state)
                        
                except Exception as e:
                    logger.error(f"Error during scan: {e}")
                    self.update_progress(0, 0, '', 'error')
                    scan_state.error_scan(str(e))
                    db.session.commit()
                    raise
        
        self.current_scan_thread = threading.Thread(target=run_scan)
        self.current_scan_thread.start()
        
        return {
            'message': 'Scan started',
            'directories': valid_dirs,
            'force_rescan': force_rescan,
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
                        force_rescan: bool, scan_state: ScanState):
        """Perform sequential scan of files"""
        total_files = len(files)
        
        for i, file_path in enumerate(files):
            if self.scan_cancelled:
                break
            
            self.update_progress(i, total_files, file_path, 'scanning')
            
            try:
                checker.scan_file(file_path, force_rescan=force_rescan)
            except Exception as e:
                logger.error(f"Error scanning file {file_path}: {e}")
            
            # Update scan state progress
            scan_state.update_progress(i + 1, total_files)
            db.session.commit()
        
        # Complete scan
        if self.scan_cancelled:
            self._handle_scan_cancellation(scan_state)
        else:
            self.update_progress(total_files, total_files, '', 'completed')
            scan_state.complete_scan()
            db.session.commit()
    
    def _parallel_scan(self, checker: PixelProbe, files: List[str], 
                      force_rescan: bool, num_workers: int, scan_state: ScanState):
        """Perform parallel scan of files"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        total_files = len(files)
        completed = 0
        
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
                scan_state.update_progress(completed, total_files)
                db.session.commit()
        
        # Complete scan
        if self.scan_cancelled:
            self._handle_scan_cancellation(scan_state)
        else:
            self.update_progress(total_files, total_files, '', 'completed')
            scan_state.complete_scan()
            db.session.commit()
    
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
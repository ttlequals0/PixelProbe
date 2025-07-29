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
from models import db, ScanResult, ScanState, ScanReport, ScanChunk
from utils import ProgressTracker
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
import hashlib

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
        self.chunk_size = 10000  # Files per chunk
        
    def is_scan_running(self) -> bool:
        """Check if a scan is currently running"""
        is_running = self.current_scan_thread is not None and self.current_scan_thread.is_alive()
        logger.debug(f"is_scan_running check: thread exists={self.current_scan_thread is not None}, "
                    f"is_alive={self.current_scan_thread.is_alive() if self.current_scan_thread else False}, "
                    f"result={is_running}")
        return is_running
    
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
                finally:
                    # Clear thread reference to allow new scans
                    self.current_scan_thread = None
                    logger.debug("Single file scan thread cleaned up")
        
        self.current_scan_thread = threading.Thread(target=run_scan, name="SingleFileScan")
        logger.info(f"Starting single file scan thread: {self.current_scan_thread.name}")
        self.current_scan_thread.start()
        
        return {'status': 'started', 'message': 'Scan started', 'file_path': file_path}
    
    def scan_directories(self, directories: List[str], force_rescan: bool = False, 
                        num_workers: int = 1, deep_scan: bool = False) -> Dict:
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
        # Store deep_scan flag for later use in report creation
        self._deep_scan = deep_scan
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
                    
                    # Clean up any existing chunks for the directories we're about to scan
                    # This prevents UNIQUE constraint failures from previous failed scans
                    logger.info(f"Cleaning up old scan chunks for directories: {valid_dirs}")
                    for directory in valid_dirs:
                        # Delete chunks for this directory and all subdirectories
                        db.session.query(ScanChunk).filter(
                            ScanChunk.directory_path.like(f"{directory}%")
                        ).delete(synchronize_session=False)
                    db.session.commit()
                    logger.info("Old scan chunks cleaned up successfully")
                    
                    excluded_paths, excluded_extensions = load_exclusions()
                    checker = PixelProbe(
                        database_path=self.database_uri,
                        excluded_paths=excluded_paths,
                        excluded_extensions=excluded_extensions
                    )
                    
                    # Create progress tracker for scan operations
                    progress_tracker = ProgressTracker('scan')
                    
                    # Log scan start
                    logger.info(f"=== SCAN STARTED ===")
                    logger.info(f"Scan ID: {scan_state.scan_id}")
                    logger.info(f"Directories: {valid_dirs}")
                    logger.info(f"Force rescan: {force_rescan}")
                    logger.info(f"Workers: {num_workers}")
                    
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
                    logger.info("Starting file discovery with efficient database filtering...")
                    
                    # For large databases, we'll pass a callback function that checks the database
                    # This avoids loading all paths into memory
                    def check_file_exists(file_path):
                        """Check if a file exists in the database"""
                        return db.session.query(ScanResult).filter_by(file_path=file_path).first() is not None
                    
                    # We'll modify the discovery to use this callback
                    # For now, let's load a reasonable subset to avoid the worst performance
                    # Get count of existing files for logging
                    existing_count = db.session.query(ScanResult).count()
                    logger.info(f"Database contains {existing_count} existing files")
                    
                    # If database is small enough, load all paths for best performance
                    if existing_count < 100000:
                        logger.info("Loading all existing paths for fast discovery...")
                        existing_files = set(row[0] for row in db.session.query(ScanResult.file_path).all())
                    else:
                        # For very large databases, we'll check during the add phase
                        logger.info("Large database detected - will handle duplicates during add phase")
                        existing_files = set()  # Empty set, duplicates handled by INSERT OR IGNORE
                    
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
                        batch_size = 1000  # Process files in larger batches
                        
                        # Process files in batches for better performance
                        for batch_start in range(0, len(all_files), batch_size):
                            if self.scan_cancelled:
                                self._handle_scan_cancellation(scan_state)
                                return
                            
                            batch_end = min(batch_start + batch_size, len(all_files))
                            batch_files = all_files[batch_start:batch_end]
                            
                            # Log batch processing start for debugging
                            if batch_start % 10000 == 0:
                                logger.info(f"Processing batch starting at {batch_start}/{len(all_files)}")
                            
                            # Add batch of files efficiently
                            try:
                                batch_added, batch_duplicates = self._add_files_batch_to_db(batch_files)
                            except Exception as e:
                                logger.error(f"Error processing batch {batch_start}-{batch_end}: {e}")
                                # Continue with next batch to avoid complete failure
                                continue
                            added_count += batch_added
                            duplicate_count += batch_duplicates
                            
                            # Update progress
                            self.update_progress(batch_end, new_files_count, batch_files[-1] if batch_files else '', 'adding')
                            scan_state.update_progress(batch_end, new_files_count, current_file=batch_files[-1] if batch_files else '')
                            
                            # Note: Commit is now done inside _add_files_batch_to_db
                            logger.info(f"Processed batch {batch_start//batch_size + 1}/{(len(all_files) + batch_size - 1)//batch_size}: Added {added_count} total, {duplicate_count} duplicates (batch end: {batch_end}/{len(all_files)})")
                            
                            # Safety check: if too many duplicates, something is wrong
                            # Check against total files actually processed (added + duplicates)
                            total_processed = added_count + duplicate_count
                            if total_processed > 1000 and duplicate_count == total_processed:
                                logger.error(f"All files are duplicates ({duplicate_count}/{total_processed}). Discovery phase may have failed.")
                                logger.error("Aborting add phase to prevent infinite loop.")
                                break
                            
                            # Periodic checkpoint to prevent transaction log bloat
                            if batch_end % 50000 == 0:
                                logger.info(f"Checkpoint at {batch_end} files - committing transaction")
                                db.session.commit()
                        
                        db.session.commit()
                        logger.info(f"Add phase completed. Added {added_count} new files out of {new_files_count} discovered")
                    
                    # Phase 3: Scanning - Check integrity of files that need scanning
                    # First count total files to scan
                    if force_rescan:
                        total_scan_files = db.session.query(ScanResult).filter(
                            db.or_(*[ScanResult.file_path.like(f"{d}%") for d in valid_dirs])
                        ).count()
                    else:
                        total_scan_files = db.session.query(ScanResult).filter(
                            ScanResult.scan_status == 'pending',
                            db.or_(*[ScanResult.file_path.like(f"{d}%") for d in valid_dirs])
                        ).count()
                    
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
                            # Determine scan type based on flags
                            if getattr(self, '_deep_scan', False):
                                scan_type = 'deep_scan'
                            elif force_rescan:
                                scan_type = 'rescan'
                            else:
                                scan_type = 'full_scan'
                            self._create_scan_report(completed_scan_state, scan_type=scan_type)
                        
                        logger.info(f"=== SCAN COMPLETED (NO FILES) ===")
                        logger.info(f"Scan ID: {scan_state_id}")
                        logger.info(f"Result: No files to process")
                        logger.info(f"=== END SCAN ===")
                        return {'message': 'Scan completed - no files to process', 'total_files': 0}
                    
                    # Create chunks only if there are files to scan
                    scan_chunks = self._create_directory_chunks(valid_dirs, scan_state.scan_id)
                    
                    # Save chunks to database
                    for chunk in scan_chunks:
                        chunk.phase = 'scanning'
                        db.session.add(chunk)
                    db.session.commit()
                    
                    logger.info(f"Starting scan phase: {total_scan_files} files to scan across {len(scan_chunks)} chunks")
                    
                    # Update both service and database state for actual scanning
                    self.update_progress(0, total_scan_files, '', 'scanning')
                    scan_state.update_progress(0, total_scan_files, phase='scanning', current_file='')
                    scan_state.progress_message = f'Phase 3 of 3: Scanning {total_scan_files} files for corruption...'
                    
                    # Explicit commit to ensure database state is updated
                    db.session.commit()
                    logger.info(f"Scan state transitioned to 'scanning' phase "
                               f"with {total_scan_files} files")
                    
                    if num_workers > 1:
                        self._parallel_scan_chunks(checker, scan_chunks, force_rescan, num_workers, scan_state, scan_state_id)
                    else:
                        self._sequential_scan_chunks(checker, scan_chunks, force_rescan, scan_state, scan_state_id)
                        
                except Exception as e:
                    logger.error(f"=== SCAN ERROR ===")
                    logger.error(f"Scan ID: {scan_state_id}")
                    logger.error(f"Phase at error: {scan_state.phase if scan_state else 'unknown'}")
                    logger.error(f"Error: {e}")
                    logger.error(f"=== END SCAN ERROR ===")
                    self.update_progress(0, 0, '', 'error')
                    if scan_state:
                        scan_state.error_scan(str(e))
                        db.session.commit()
                    raise
                finally:
                    # Clear thread reference to allow new scans
                    self.current_scan_thread = None
                    logger.info("Scan thread cleaned up")
        
        self.current_scan_thread = threading.Thread(target=run_scan, name="DirectoryScan")
        logger.info(f"Starting directory scan thread: {self.current_scan_thread.name}")
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
        # Store deep_scan flag for later use in report creation
        self._deep_scan = deep_scan
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
                    
                    # For large file lists, use chunking
                    if total_files > 100:
                        # Group files by directory for chunking
                        files_by_dir = {}
                        for file_path in valid_files:
                            dir_path = os.path.dirname(file_path)
                            if dir_path not in files_by_dir:
                                files_by_dir[dir_path] = []
                            files_by_dir[dir_path].append(file_path)
                        
                        # Create chunks for each directory
                        chunks = []
                        for dir_path, files in files_by_dir.items():
                            chunk_id = hashlib.md5(f"{scan_state.scan_id}:{dir_path}".encode()).hexdigest()
                            chunk = ScanChunk(
                                scan_id=scan_state.scan_id,
                                chunk_id=chunk_id,
                                directory_path=dir_path,
                                phase='scanning',
                                status='pending'
                            )
                            db.session.add(chunk)
                            chunks.append(chunk)
                        db.session.commit()
                        
                        logger.info(f"Created {len(chunks)} chunks for {total_files} files")
                        
                        # Update scan state
                        self.update_progress(0, len(chunks), '', 'scanning')
                        scan_state.phase = 'scanning'
                        scan_state.phase_number = 3
                        scan_state.phase_current = 0
                        scan_state.phase_total = total_files
                        scan_state.total_chunks = len(chunks)
                        scan_state.start_time = datetime.now(timezone.utc)
                        scan_state.progress_message = f'Scanning {total_files} selected files across {len(chunks)} directories...'
                        db.session.commit()
                        
                        # For selected files, we need a special chunk processor
                        if num_workers > 1:
                            self._parallel_scan_selected_chunks(checker, chunks, valid_files, force_rescan, num_workers, scan_state, scan_state_id)
                        else:
                            self._sequential_scan_selected_chunks(checker, chunks, valid_files, force_rescan, scan_state, scan_state_id)
                    else:
                        # For small file lists, use the original method
                        self.update_progress(0, total_files, '', 'scanning')
                        scan_state.phase = 'scanning'
                        scan_state.phase_number = 3
                        scan_state.phase_current = 0
                        scan_state.phase_total = total_files
                        scan_state.start_time = datetime.now(timezone.utc)
                        scan_state.progress_message = f'Scanning {total_files} selected files for corruption...'
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
                finally:
                    # Clear thread reference to allow new scans
                    self.current_scan_thread = None
                    logger.info("File scan thread cleaned up")
        
        self.current_scan_thread = threading.Thread(target=run_scan, name="FileListScan")
        logger.info(f"Starting file list scan thread: {self.current_scan_thread.name}")
        self.current_scan_thread.start()
        
        return {
            'status': 'started',
            'message': f'Scan started for {len(valid_files)} files',
            'files': len(valid_files),
            'force_rescan': force_rescan,
            'deep_scan': deep_scan,
            'num_workers': num_workers
        }
    
    def resume_scan(self, scan_id: str = None) -> Dict:
        """Resume a previously interrupted scan"""
        if self.is_scan_running():
            raise RuntimeError("Another scan is already in progress")
        
        # Find the scan to resume
        if scan_id:
            scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
        else:
            # Find the most recent incomplete scan
            scan_state = ScanState.query.filter(
                ScanState.phase != 'completed',
                ScanState.phase != 'cancelled'
            ).order_by(ScanState.start_time.desc()).first()
        
        if not scan_state:
            raise ValueError("No resumable scan found")
        
        # Get incomplete chunks
        incomplete_chunks = self._get_resumable_chunks(scan_state.scan_id)
        if not incomplete_chunks:
            return {'message': 'No incomplete chunks found', 'scan_id': scan_state.scan_id}
        
        logger.info(f"Resuming scan {scan_state.scan_id} with {len(incomplete_chunks)} incomplete chunks")
        
        # Update scan state
        scan_state.is_active = True
        scan_state.error_message = None
        db.session.commit()
        
        # Initialize progress
        self.update_progress(0, len(incomplete_chunks), '', 'resuming')
        self.scan_cancelled = False
        
        # Capture context for thread
        scan_state_id = scan_state.id
        app = current_app._get_current_object()
        
        def run_resume():
            with app.app_context():
                try:
                    # Re-fetch scan state in thread context
                    scan_state = db.session.get(ScanState, scan_state_id)
                    if not scan_state:
                        logger.error(f"Could not find scan state {scan_state_id}")
                        return
                    
                    # Initialize checker
                    excluded_paths, excluded_extensions = load_exclusions()
                    checker = PixelProbe(
                        database_path=self.database_uri,
                        excluded_paths=excluded_paths,
                        excluded_extensions=excluded_extensions
                    )
                    
                    # Process incomplete chunks
                    completed = 0
                    for chunk in incomplete_chunks:
                        if self.scan_cancelled:
                            self._handle_scan_cancellation(scan_state)
                            return
                        
                        logger.info(f"Processing chunk {chunk.chunk_id} in phase {scan_state.phase}")
                        
                        # For scanning phase, we only scan files already in DB
                        if scan_state.phase == 'scanning':
                            # For resume, we don't have cumulative counts readily available
                            self._scan_chunk_files(chunk, checker, scan_state.force_rescan, 0, 0, scan_state)
                        else:
                            # For discovery/adding phases, use the full process
                            self._process_chunk(chunk, checker, scan_state.phase, scan_state.force_rescan)
                        
                        completed += 1
                        self.update_progress(completed, len(incomplete_chunks), 
                                           chunk.directory_path, 'processing')
                    
                    # Mark scan as completed
                    scan_state.phase = 'completed'
                    scan_state.is_active = False
                    scan_state.end_time = datetime.now(timezone.utc)
                    db.session.commit()
                    
                    self.update_progress(len(incomplete_chunks), len(incomplete_chunks), 
                                       '', 'completed')
                    
                except Exception as e:
                    logger.error(f"Error during resume: {e}")
                    self.update_progress(0, 0, '', 'error')
                    scan_state.error_scan(str(e))
                    db.session.commit()
                finally:
                    # Clear thread reference to allow new scans
                    self.current_scan_thread = None
                    logger.info("Resume scan thread cleaned up")
        
        self.current_scan_thread = threading.Thread(target=run_resume, name="ResumeScan")
        logger.info(f"Starting resume scan thread: {self.current_scan_thread.name}")
        self.current_scan_thread.start()
        
        return {
            'status': 'resumed',
            'message': f'Resumed scan with {len(incomplete_chunks)} chunks to process',
            'scan_id': scan_state.scan_id,
            'chunks_remaining': len(incomplete_chunks)
        }
    
    def cancel_scan(self) -> Dict:
        """Cancel the current scan"""
        if not self.is_scan_running():
            raise RuntimeError("No scan is currently running")
        
        self.scan_cancelled = True
        logger.info("Scan cancellation requested")
        
        # Update scan state in database
        try:
            scan_state = ScanState.get_or_create()
            scan_state.cancel_scan()
            db.session.commit()
            logger.info("Scan state updated to cancelled in database")
        except Exception as e:
            logger.error(f"Error updating scan state: {e}")
        
        # The scan threads will check self.scan_cancelled flag and stop
        # Wait a moment for threads to notice the cancellation
        import time
        time.sleep(0.5)
        
        # Force progress update to show cancelled state
        self.update_progress(
            self.scan_progress['current'],
            self.scan_progress['total'],
            '',
            'cancelled'
        )
        
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
    
    def _sequential_scan_chunks(self, checker: PixelProbe, chunks: List[ScanChunk], 
                               force_rescan: bool, scan_state: ScanState, scan_state_id: int):
        """Perform sequential scan of chunks"""
        total_chunks = len(chunks)
        total_files_scanned = 0
        total_files_to_scan = scan_state.phase_total  # Total files from scanning phase
        
        # Create progress tracker for scan
        progress_tracker = ProgressTracker('scan')
        
        for i, chunk in enumerate(chunks):
            if self.scan_cancelled:
                break
            
            logger.info(f"Processing chunk {i+1}/{total_chunks}: {chunk.directory_path}")
            
            # Get initial files scanned count for this chunk
            initial_scanned = total_files_scanned
            
            # Scan files in this chunk
            self._scan_chunk_files(chunk, checker, force_rescan, total_files_scanned, total_files_to_scan, scan_state)
            
            # Update total files scanned based on chunk results
            if chunk.files_scanned:
                total_files_scanned += chunk.files_scanned
            
            # Update progress with actual file counts
            self.update_progress(total_files_scanned, total_files_to_scan, chunk.directory_path, 'scanning')
            
            # Update scan state progress with files, not chunks
            scan_state.current_chunk_index = i + 1
            scan_state.files_processed = total_files_scanned  # Ensure files_processed is set
            scan_state.update_progress(total_files_scanned, total_files_to_scan, current_file=chunk.directory_path)
            
            # Update progress message
            scan_state.progress_message = progress_tracker.get_progress_message(
                f'Phase 3 of 3: Scanning files across {total_chunks} directories',
                total_files_scanned,
                total_files_to_scan,
                os.path.basename(chunk.directory_path)
            )
            db.session.commit()
            
            logger.info(f"Chunk {i+1}/{total_chunks} completed: {chunk.files_scanned} files scanned (total: {total_files_scanned}/{total_files_to_scan})")
        
        # Complete scan
        if self.scan_cancelled:
            self._handle_scan_cancellation(scan_state)
        else:
            self.update_progress(total_files_scanned, total_files_to_scan, '', 'completed')
            
            # Thread-safe completion using direct SQL update
            from sqlalchemy import text
            db.session.execute(
                text("UPDATE scan_state SET phase = 'completed', is_active = false, end_time = :end_time WHERE id = :id"),
                {'end_time': datetime.now(timezone.utc), 'id': scan_state_id}
            )
            db.session.commit()
            
            # Create scan report
            completed_scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
            if completed_scan_state:
                scan_type = 'deep_scan' if getattr(self, '_deep_scan', False) else 'rescan' if force_rescan else 'full_scan'
                self._create_scan_report(completed_scan_state, scan_type=scan_type)
                
                logger.info(f"=== SCAN COMPLETED (SEQUENTIAL) ===")
                logger.info(f"Scan ID: {scan_state_id}")
                logger.info(f"Total chunks processed: {total_chunks}")
                logger.info(f"Files scanned: {total_files_scanned}/{total_files_to_scan}")
                logger.info(f"=== END SCAN ===")
    
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
                # Determine scan type based on flags
                if getattr(self, '_deep_scan', False):
                    scan_type = 'deep_scan'
                elif force_rescan:
                    scan_type = 'rescan'
                else:
                    scan_type = 'full_scan'
                self._create_scan_report(completed_scan_state, scan_type=scan_type)
    
    def _parallel_scan_chunks(self, checker: PixelProbe, chunks: List[ScanChunk],
                             force_rescan: bool, num_workers: int, scan_state: ScanState, scan_state_id: int):
        """Perform parallel scan of chunks"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        
        total_chunks = len(chunks)
        completed_chunks = 0
        total_files_scanned = 0
        total_files_to_scan = scan_state.phase_total  # Total files from scanning phase
        files_scanned_lock = threading.Lock()
        
        # Create progress tracker for scan
        progress_tracker = ProgressTracker('scan')
        
        def scan_chunk(chunk):
            if self.scan_cancelled:
                return None
            # For parallel scan, we can't pass cumulative counts, so pass 0
            # The main thread will handle updating the cumulative progress
            self._scan_chunk_files(chunk, checker, force_rescan, 0, 0, scan_state)
            return chunk, chunk.files_scanned or 0
        
        with ThreadPoolExecutor(max_workers=min(num_workers, len(chunks))) as executor:
            # Submit all chunks for scanning
            future_to_chunk = {executor.submit(scan_chunk, chunk): chunk for chunk in chunks}
            
            # Process completed scans
            for future in as_completed(future_to_chunk):
                if self.scan_cancelled:
                    executor.shutdown(wait=False)
                    break
                
                try:
                    chunk, files_in_chunk = future.result()
                    completed_chunks += 1
                    
                    # Update total files scanned thread-safely
                    with files_scanned_lock:
                        total_files_scanned += files_in_chunk
                        current_files_scanned = total_files_scanned
                    
                    self.update_progress(current_files_scanned, total_files_to_scan, chunk.directory_path, 'scanning')
                    
                    # Update scan state progress with file counts
                    scan_state.current_chunk_index = completed_chunks
                    scan_state.files_processed = current_files_scanned  # Ensure files_processed is set
                    scan_state.update_progress(current_files_scanned, total_files_to_scan, current_file=chunk.directory_path)
                    
                    # Update progress message
                    scan_state.progress_message = progress_tracker.get_progress_message(
                        f'Phase 3 of 3: Scanning files across {total_chunks} directories (parallel)',
                        current_files_scanned,
                        total_files_to_scan,
                        os.path.basename(chunk.directory_path)
                    )
                    db.session.commit()
                    
                    logger.info(f"Parallel scan progress: {completed_chunks}/{total_chunks} chunks processed, {current_files_scanned}/{total_files_to_scan} files scanned")
                except Exception as e:
                    logger.error(f"Error processing chunk result: {e}")
        
        # Complete scan
        if self.scan_cancelled:
            self._handle_scan_cancellation(scan_state)
        else:
            self.update_progress(total_files_scanned, total_files_to_scan, '', 'completed')
            
            # Thread-safe completion using direct SQL update
            from sqlalchemy import text
            db.session.execute(
                text("UPDATE scan_state SET phase = 'completed', is_active = false, end_time = :end_time WHERE id = :id"),
                {'end_time': datetime.now(timezone.utc), 'id': scan_state_id}
            )
            db.session.commit()
            
            # Create scan report
            completed_scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
            if completed_scan_state:
                scan_type = 'deep_scan' if getattr(self, '_deep_scan', False) else 'rescan' if force_rescan else 'full_scan'
                self._create_scan_report(completed_scan_state, scan_type=scan_type)
                
                logger.info(f"=== SCAN COMPLETED (PARALLEL) ===")
                logger.info(f"Scan ID: {scan_state_id}")
                logger.info(f"Total chunks processed: {completed_chunks}")
                logger.info(f"Files scanned: {total_files_scanned}/{total_files_to_scan}")
                logger.info(f"Workers used: {num_workers}")
                logger.info(f"=== END SCAN ===")
    
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
                # Determine scan type based on flags
                if getattr(self, '_deep_scan', False):
                    scan_type = 'deep_scan'
                elif force_rescan:
                    scan_type = 'rescan'
                else:
                    scan_type = 'full_scan'
                self._create_scan_report(completed_scan_state, scan_type=scan_type)
    
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
        logger.info(f"=== SCAN CANCELLATION INITIATED ===")
        logger.info(f"Scan ID: {scan_state.scan_id}")
        logger.info(f"Phase at cancellation: {scan_state.phase}")
        logger.info(f"Files processed: {scan_state.files_processed}/{scan_state.estimated_total}")
        
        # Update progress
        self.update_progress(
            self.scan_progress['current'],
            self.scan_progress['total'],
            '',
            'cancelled'
        )
        
        # Update scan state
        scan_state.cancel_scan()
        
        # Clean up any files stuck in 'scanning' state
        from models import ScanResult
        stuck_count = ScanResult.query.filter_by(scan_status='scanning').update(
            {'scan_status': 'pending'},
            synchronize_session=False
        )
        
        if stuck_count > 0:
            logger.info(f"Reset {stuck_count} files from 'scanning' to 'pending' state")
        
        db.session.commit()
        logger.info(f"=== SCAN CANCELLATION COMPLETE (ID: {scan_state.scan_id}) ===")
    
    def _add_files_batch_to_db(self, file_paths: List[str]) -> Tuple[int, int]:
        """Add a batch of files to the database efficiently
        
        Returns:
            Tuple[int, int]: (files_added, duplicates_found)
        """
        import os
        import magic
        from datetime import datetime
        from models import ScanResult
        from sqlalchemy.exc import IntegrityError
        
        added_count = 0
        duplicate_count = 0
        files_to_insert = []
        
        for file_path in file_paths:
            try:
                # Get file stats
                stat = os.stat(file_path)
                file_size = stat.st_size
                mod_time = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                
                # Detect MIME type
                mime_type = magic.from_file(file_path, mime=True)
                
                # Skip MD5 hash during discovery for performance
                # It will be calculated during actual scan if needed
                
                files_to_insert.append({
                    'file_path': file_path,
                    'file_size': file_size,
                    'file_type': mime_type,
                    'last_modified': mod_time,
                    'discovered_date': datetime.now(timezone.utc),
                    'scan_status': 'pending',
                    'is_corrupted': False,
                    'marked_as_good': False,
                    'file_exists': True
                })
                
            except Exception as e:
                logger.error(f"Failed to get file info: {file_path} - {e}")
                # Create minimal entry for files with errors
                files_to_insert.append({
                    'file_path': file_path,
                    'discovered_date': datetime.now(timezone.utc),
                    'scan_status': 'error',
                    'error_message': str(e),
                    'is_corrupted': False,
                    'marked_as_good': False,
                    'file_exists': True
                })
        
        # Bulk insert with duplicate handling
        if files_to_insert:
            try:
                # For SQLite, use INSERT OR IGNORE
                if 'sqlite' in str(db.engine.url):
                    # Separate files with errors from successful ones
                    files_with_errors = [f for f in files_to_insert if 'error_message' in f]
                    files_without_errors = [f for f in files_to_insert if 'error_message' not in f]
                    
                    # Insert files without errors (most common case)
                    if files_without_errors:
                        # For SQLite with INSERT OR IGNORE, we need to check which files were actually inserted
                        # First, get the file paths we're trying to insert
                        file_paths_to_insert = [f['file_path'] for f in files_without_errors]
                        
                        # Check which ones already exist
                        # For large batches, check in smaller chunks to avoid query size limits
                        existing_before = set()
                        chunk_size = 100  # Process IN queries in smaller chunks
                        for i in range(0, len(file_paths_to_insert), chunk_size):
                            chunk_paths = file_paths_to_insert[i:i+chunk_size]
                            existing_chunk = set(row[0] for row in db.session.query(ScanResult.file_path).filter(
                                ScanResult.file_path.in_(chunk_paths)
                            ).all())
                            existing_before.update(existing_chunk)
                        
                        # Execute the INSERT OR IGNORE using bulk insert
                        from sqlalchemy import insert
                        stmt = insert(ScanResult).values(files_without_errors)
                        # For SQLite, use INSERT OR IGNORE
                        stmt = stmt.prefix_with("OR IGNORE")
                        db.session.execute(stmt)
                        db.session.commit()
                        
                        # Check which ones exist now
                        # For large batches, check in smaller chunks to avoid query size limits
                        existing_after = set()
                        for i in range(0, len(file_paths_to_insert), chunk_size):
                            chunk_paths = file_paths_to_insert[i:i+chunk_size]
                            existing_chunk = set(row[0] for row in db.session.query(ScanResult.file_path).filter(
                                ScanResult.file_path.in_(chunk_paths)
                            ).all())
                            existing_after.update(existing_chunk)
                        
                        # Calculate actual added
                        actual_added = len(existing_after) - len(existing_before)
                        added_count += actual_added
                        
                        # Track duplicates for files without errors
                        duplicate_count += len(existing_before)
                        
                        logger.info(f"Batch insert: attempted {len(files_without_errors)}, already existed {len(existing_before)}, actually added {actual_added}")
                    
                    # Insert files with errors separately
                    if files_with_errors:
                        # Get file paths for error files
                        error_file_paths = [f['file_path'] for f in files_with_errors]
                        
                        # Check which ones already exist
                        existing_error_before = set(row[0] for row in db.session.query(ScanResult.file_path).filter(
                            ScanResult.file_path.in_(error_file_paths)
                        ).all())
                        
                        # Execute bulk insert for files with errors
                        from sqlalchemy import insert
                        stmt_error = insert(ScanResult).values(files_with_errors)
                        # For SQLite, use INSERT OR IGNORE
                        stmt_error = stmt_error.prefix_with("OR IGNORE")
                        db.session.execute(stmt_error)
                        db.session.commit()
                        
                        # Check which ones exist now
                        existing_error_after = set(row[0] for row in db.session.query(ScanResult.file_path).filter(
                            ScanResult.file_path.in_(error_file_paths)
                        ).all())
                        
                        actual_added = len(existing_error_after) - len(existing_error_before)
                        added_count += actual_added
                        
                        # Track duplicates for files with errors
                        duplicate_count += len(existing_error_before)
                        
                        logger.info(f"Error batch insert: attempted {len(files_with_errors)}, already existed {len(existing_error_before)}, actually added {actual_added}")
                else:
                    # For other databases, insert one by one (less efficient)
                    for file_data in files_to_insert:
                        try:
                            scan_result = ScanResult(**file_data)
                            db.session.add(scan_result)
                            db.session.flush()
                            added_count += 1
                        except IntegrityError:
                            db.session.rollback()
                            duplicate_count += 1
                            
            except Exception as e:
                logger.error(f"Error during batch insert: {e}")
                db.session.rollback()
                # Fall back to individual inserts
                for file_data in files_to_insert:
                    try:
                        # Check if exists first
                        existing = db.session.query(ScanResult).filter_by(
                            file_path=file_data['file_path']
                        ).first()
                        if not existing:
                            scan_result = ScanResult(**file_data)
                            db.session.add(scan_result)
                            added_count += 1
                        else:
                            duplicate_count += 1
                    except Exception as e2:
                        logger.error(f"Failed to add file: {file_data['file_path']} - {e2}")
        
        return added_count, duplicate_count
    
    def _create_directory_chunks(self, directories: List[str], scan_id: str) -> List[ScanChunk]:
        """Create chunks based on directory structure for better organization"""
        chunks = []
        
        # Get all subdirectories up to 2 levels deep for chunking
        all_dirs = set()
        for base_dir in directories:
            all_dirs.add(base_dir)
            try:
                # Add immediate subdirectories
                for entry in os.scandir(base_dir):
                    if entry.is_dir() and not entry.name.startswith('.'):
                        all_dirs.add(entry.path)
                        # Add second level subdirectories for large structures
                        try:
                            for sub_entry in os.scandir(entry.path):
                                if sub_entry.is_dir() and not sub_entry.name.startswith('.'):
                                    all_dirs.add(sub_entry.path)
                        except (OSError, PermissionError):
                            pass
            except (OSError, PermissionError):
                logger.warning(f"Cannot access directory: {base_dir}")
        
        # Create chunks for each directory
        for dir_path in sorted(all_dirs):
            chunk_id = hashlib.md5(f"{scan_id}:{dir_path}".encode()).hexdigest()
            chunk = ScanChunk(
                scan_id=scan_id,
                chunk_id=chunk_id,
                directory_path=dir_path,
                phase='pending',
                status='pending'
            )
            chunks.append(chunk)
        
        return chunks
    
    def _get_resumable_chunks(self, scan_id: str) -> List[ScanChunk]:
        """Get chunks that need to be processed for resuming a scan"""
        # Get all non-completed chunks for this scan
        return ScanChunk.query.filter_by(
            scan_id=scan_id
        ).filter(
            ScanChunk.status != 'completed'
        ).order_by(ScanChunk.directory_path).all()
    
    def _process_chunk(self, chunk: ScanChunk, checker: PixelProbe, 
                      phase: str, force_rescan: bool = False) -> Dict:
        """Process a single chunk of files"""
        chunk.status = 'processing'
        chunk.phase = phase
        chunk.start_time = datetime.now(timezone.utc)
        db.session.commit()
        
        try:
            if phase == 'discovering':
                # Discover files in this chunk's directory only
                files = checker.discover_media_files([chunk.directory_path], 
                                                   existing_files=set())
                chunk.files_discovered = len(files)
                chunk.status = 'completed'
                chunk.end_time = datetime.now(timezone.utc)
                db.session.commit()
                return {'files': files, 'count': len(files)}
                
            elif phase == 'adding':
                # Add files from this directory
                files_in_dir = []
                for root, _, filenames in os.walk(chunk.directory_path):
                    for filename in filenames:
                        files_in_dir.append(os.path.join(root, filename))
                
                # Filter to only media files
                media_files = [f for f in files_in_dir if checker._is_supported_file(f)]
                
                # Add in batches
                added, duplicates = self._add_files_batch_to_db(media_files)
                chunk.files_added = added
                chunk.status = 'completed'
                chunk.end_time = datetime.now(timezone.utc)
                db.session.commit()
                return {'added': added, 'duplicates': duplicates}
                
            elif phase == 'scanning':
                # Scan files in this directory
                files_to_scan = db.session.query(ScanResult).filter(
                    ScanResult.file_path.like(f"{chunk.directory_path}%"),
                    ScanResult.scan_status == 'pending'
                ).all()
                
                scanned = 0
                for file_result in files_to_scan:
                    if self.scan_cancelled:
                        break
                    try:
                        checker.scan_file(file_result.file_path, force_rescan=force_rescan)
                        scanned += 1
                    except Exception as e:
                        logger.error(f"Error scanning {file_result.file_path}: {e}")
                
                chunk.files_scanned = scanned
                chunk.status = 'completed'
                chunk.end_time = datetime.now(timezone.utc)
                db.session.commit()
                return {'scanned': scanned}
                
        except Exception as e:
            chunk.status = 'error'
            chunk.error_message = str(e)
            chunk.end_time = datetime.now(timezone.utc)
            db.session.commit()
            logger.error(f"Error processing chunk {chunk.chunk_id}: {e}")
            return {'error': str(e)}
    
    def _scan_chunk_files(self, chunk: ScanChunk, checker: PixelProbe, force_rescan: bool = False, 
                          total_scanned_so_far: int = 0, total_to_scan: int = 0, scan_state: ScanState = None):
        """Scan files in a chunk that are already in the database"""
        chunk.status = 'processing'
        chunk.phase = 'scanning'
        chunk.start_time = datetime.now(timezone.utc)
        db.session.commit()
        
        try:
            # Query for files in this chunk's directory that need scanning
            # Use proper path matching to avoid overlaps between chunks
            # Ensure the path ends with a separator to avoid /path/to/dir matching /path/to/dir2
            chunk_path_pattern = chunk.directory_path.rstrip(os.sep) + os.sep + '%'
            
            if force_rescan:
                # Rescan all files in directory
                files_to_scan = db.session.query(ScanResult).filter(
                    db.or_(
                        ScanResult.file_path == chunk.directory_path,  # Exact match for files in root
                        ScanResult.file_path.like(chunk_path_pattern)  # Files in subdirectories
                    )
                ).all()
            else:
                # Only scan pending files
                files_to_scan = db.session.query(ScanResult).filter(
                    db.or_(
                        db.and_(
                            ScanResult.file_path == chunk.directory_path,
                            ScanResult.scan_status == 'pending'
                        ),
                        db.and_(
                            ScanResult.file_path.like(chunk_path_pattern),
                            ScanResult.scan_status == 'pending'
                        )
                    )
                ).all()
            
            logger.info(f"Chunk {chunk.chunk_id}: Found {len(files_to_scan)} files to scan in {chunk.directory_path}")
            
            scanned = 0
            errors = 0
            
            for file_result in files_to_scan:
                if self.scan_cancelled:
                    chunk.status = 'cancelled'
                    chunk.end_time = datetime.now(timezone.utc)
                    db.session.commit()
                    return
                
                try:
                    # Scan the file
                    checker.scan_file(file_result.file_path, force_rescan=force_rescan)
                    scanned += 1
                    
                    # Update progress with cumulative counts
                    current_total = total_scanned_so_far + scanned
                    if scanned % 10 == 0 or scanned == 1:  # Update on first file and every 10
                        self.update_progress(current_total, total_to_scan, 
                                           file_result.file_path, 'scanning')
                        
                        # Update scan state if provided
                        if scan_state:
                            scan_state.files_processed = current_total
                            scan_state.update_progress(current_total, total_to_scan, current_file=file_result.file_path)
                            db.session.commit()
                        
                except Exception as e:
                    logger.error(f"Error scanning {file_result.file_path}: {e}")
                    errors += 1
            
            chunk.files_scanned = scanned
            chunk.status = 'completed'
            chunk.end_time = datetime.now(timezone.utc)
            
            # Final update to scan state
            if scan_state and scanned > 0:
                final_total = total_scanned_so_far + scanned
                scan_state.files_processed = final_total
                scan_state.update_progress(final_total, total_to_scan, current_file=chunk.directory_path)
            
            db.session.commit()
            
            logger.info(f"Chunk {chunk.chunk_id} completed: {scanned} files scanned, {errors} errors")
            
        except Exception as e:
            chunk.status = 'error'
            chunk.error_message = str(e)
            chunk.end_time = datetime.now(timezone.utc)
            db.session.commit()
            logger.error(f"Error scanning chunk {chunk.chunk_id}: {e}")
    
    def _sequential_scan_selected_chunks(self, checker: PixelProbe, chunks: List[ScanChunk], 
                                       selected_files: List[str], force_rescan: bool, 
                                       scan_state: ScanState, scan_state_id: int):
        """Scan selected files organized by chunks"""
        # Create a set for fast lookup
        selected_files_set = set(selected_files)
        total_chunks = len(chunks)
        files_scanned = 0
        
        # Create progress tracker
        progress_tracker = ProgressTracker('scan')
        
        for i, chunk in enumerate(chunks):
            if self.scan_cancelled:
                break
            
            # Update chunk status
            chunk.status = 'processing'
            chunk.phase = 'scanning'
            chunk.start_time = datetime.now(timezone.utc)
            db.session.commit()
            
            # Scan only the selected files in this chunk
            chunk_scanned = 0
            for file_path in selected_files:
                if self.scan_cancelled:
                    break
                    
                # Check if file belongs to this chunk's directory
                if file_path.startswith(chunk.directory_path + os.sep) or os.path.dirname(file_path) == chunk.directory_path:
                    try:
                        checker.scan_file(file_path, force_rescan=force_rescan)
                        chunk_scanned += 1
                        files_scanned += 1
                        
                        # Update progress
                        self.update_progress(files_scanned, len(selected_files), file_path, 'scanning')
                        
                    except Exception as e:
                        logger.error(f"Error scanning {file_path}: {e}")
            
            # Update chunk completion
            chunk.files_scanned = chunk_scanned
            chunk.status = 'completed'
            chunk.end_time = datetime.now(timezone.utc)
            
            # Update scan state
            scan_state.current_chunk_index = i + 1
            scan_state.update_progress(files_scanned, len(selected_files), current_file=chunk.directory_path)
            scan_state.progress_message = progress_tracker.get_progress_message(
                f'Scanning {len(selected_files)} selected files',
                files_scanned,
                len(selected_files),
                os.path.basename(chunk.directory_path) if chunk_scanned > 0 else "Processing..."
            )
            db.session.commit()
        
        # Complete scan
        if self.scan_cancelled:
            self._handle_scan_cancellation(scan_state)
        else:
            self.update_progress(len(selected_files), len(selected_files), '', 'completed')
            
            # Thread-safe completion
            from sqlalchemy import text
            db.session.execute(
                text("UPDATE scan_state SET phase = 'completed', is_active = false, end_time = :end_time WHERE id = :id"),
                {'end_time': datetime.now(timezone.utc), 'id': scan_state_id}
            )
            db.session.commit()
            
            # Create scan report
            completed_scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
            if completed_scan_state:
                scan_type = 'deep_scan' if getattr(self, '_deep_scan', False) else 'rescan'
                self._create_scan_report(completed_scan_state, scan_type=scan_type)
    
    def _parallel_scan_selected_chunks(self, checker: PixelProbe, chunks: List[ScanChunk],
                                     selected_files: List[str], force_rescan: bool, num_workers: int,
                                     scan_state: ScanState, scan_state_id: int):
        """Parallel scan of selected files organized by chunks"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        
        # Thread-safe counter
        files_scanned_lock = threading.Lock()
        files_scanned = 0
        selected_files_set = set(selected_files)
        
        # Create progress tracker
        progress_tracker = ProgressTracker('scan')
        
        def scan_chunk_files(chunk):
            nonlocal files_scanned
            if self.scan_cancelled:
                return 0
                
            # Update chunk status
            chunk.status = 'processing'
            chunk.phase = 'scanning'
            chunk.start_time = datetime.now(timezone.utc)
            db.session.commit()
            
            chunk_scanned = 0
            # Scan only selected files in this chunk
            for file_path in selected_files:
                if self.scan_cancelled:
                    break
                    
                # Check if file belongs to this chunk
                if file_path.startswith(chunk.directory_path + os.sep) or os.path.dirname(file_path) == chunk.directory_path:
                    try:
                        checker.scan_file(file_path, force_rescan=force_rescan)
                        chunk_scanned += 1
                        
                        with files_scanned_lock:
                            files_scanned += 1
                            self.update_progress(files_scanned, len(selected_files), file_path, 'scanning')
                            
                    except Exception as e:
                        logger.error(f"Error scanning {file_path}: {e}")
            
            # Update chunk completion
            chunk.files_scanned = chunk_scanned
            chunk.status = 'completed'
            chunk.end_time = datetime.now(timezone.utc)
            db.session.commit()
            
            return chunk_scanned
        
        # Process chunks in parallel
        with ThreadPoolExecutor(max_workers=min(num_workers, len(chunks))) as executor:
            future_to_chunk = {executor.submit(scan_chunk_files, chunk): chunk for chunk in chunks}
            
            completed_chunks = 0
            for future in as_completed(future_to_chunk):
                if self.scan_cancelled:
                    executor.shutdown(wait=False)
                    break
                    
                chunk = future_to_chunk[future]
                completed_chunks += 1
                
                # Update scan state
                scan_state.current_chunk_index = completed_chunks
                scan_state.update_progress(files_scanned, len(selected_files), current_file=chunk.directory_path)
                scan_state.progress_message = progress_tracker.get_progress_message(
                    f'Scanning {len(selected_files)} selected files (parallel)',
                    files_scanned,
                    len(selected_files),
                    f"Completed {completed_chunks}/{len(chunks)} directories"
                )
                db.session.commit()
        
        # Complete scan
        if self.scan_cancelled:
            self._handle_scan_cancellation(scan_state)
        else:
            self.update_progress(len(selected_files), len(selected_files), '', 'completed')
            
            # Thread-safe completion
            from sqlalchemy import text
            db.session.execute(
                text("UPDATE scan_state SET phase = 'completed', is_active = false, end_time = :end_time WHERE id = :id"),
                {'end_time': datetime.now(timezone.utc), 'id': scan_state_id}
            )
            db.session.commit()
            
            # Create scan report
            completed_scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
            if completed_scan_state:
                scan_type = 'deep_scan' if getattr(self, '_deep_scan', False) else 'rescan'
                self._create_scan_report(completed_scan_state, scan_type=scan_type)
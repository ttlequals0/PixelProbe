"""
Scan service for handling media scanning operations
"""

import os
import json
import threading
import logging
import traceback
from datetime import datetime, timezone
import time
from typing import List, Dict, Optional, Tuple

from flask import current_app
from media_checker import PixelProbe, load_exclusions, load_exclusions_with_patterns
from models import db, ScanResult, ScanState, ScanReport, ScanChunk
from utils import ProgressTracker
from sqlalchemy import text
import hashlib

logger = logging.getLogger(__name__)

class ScanService:
    """Service for managing scan operations"""
    
    def __init__(self, database_uri: str):
        self.database_uri = database_uri
        self.current_scan_thread: Optional[threading.Thread] = None
        self.scan_cancelled = False
        self.scan_cancel_lock = threading.Lock()  # Thread safety for cancellation
        self.scan_progress = {
            'current': 0,
            'total': 0,
            'file': '',
            'status': 'idle'
        }
        self.progress_lock = threading.Lock()
        # Get chunk size from environment or use default
        import os
        self.chunk_size = int(os.environ.get('CHUNK_SIZE', '10000'))  # Files per chunk
        
    def is_scan_running(self) -> bool:
        """Check if a scan is currently running"""
        # Check thread-based scanning
        thread_running = self.current_scan_thread is not None and self.current_scan_thread.is_alive()
        
        # Check database for active scan (covers Celery-based scans)
        db_scan_active = False
        try:
            scan_state = ScanState.get_or_create()
            db_scan_active = scan_state.is_active and scan_state.phase in ['discovering', 'adding', 'scanning']
        except Exception as e:
            logger.debug(f"Could not check database scan state: {e}")
        
        is_running = thread_running or db_scan_active
        
        logger.debug(f"is_scan_running check: thread_running={thread_running}, "
                    f"db_scan_active={db_scan_active}, result={is_running}")
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
                    excluded_paths, excluded_extensions, excluded_patterns = load_exclusions_with_patterns()
                    checker = PixelProbe(
                        database_path=self.database_uri,
                        excluded_paths=excluded_paths,
                        excluded_extensions=excluded_extensions,
                        excluded_patterns=excluded_patterns
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
                        num_workers: int = 1, async_mode: bool = True) -> Dict:
        """Scan multiple directories"""
        if self.is_scan_running():
            raise RuntimeError("Another scan is already in progress")
        
        # Check for special pending files scan marker
        is_pending_scan = len(directories) == 1 and directories[0] == 'PENDING_FILES_SCAN'
        
        # Validate directories (skip for pending scan)
        if is_pending_scan:
            valid_dirs = ['PENDING_FILES_SCAN']
        else:
            valid_dirs = [d for d in directories if os.path.exists(d)]
            if not valid_dirs:
                raise ValueError("No valid directories provided")
        
        # Initialize progress
        self.update_progress(0, 0, '', 'initializing')
        self.scan_cancelled = False
        
        # Save scan state and capture ID before threading
        # Create a new scan state for this scan instead of reusing existing one
        scan_state = ScanState.create_new_scan()
        scan_state.start_scan(valid_dirs, force_rescan)
        # Safely set num_workers if column exists
        if hasattr(scan_state, 'num_workers'):
            scan_state.num_workers = num_workers  # Track the number of workers used
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
                    scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                    if not scan_state:
                        logger.error(f"Could not find scan state with ID {scan_state_id}")
                        return
                    
                    # Clean up any existing chunks for the directories we're about to scan
                    # This prevents UNIQUE constraint failures from previous failed scans
                    logger.info(f"Cleaning up old scan chunks for directories: {valid_dirs}")
                    for directory in valid_dirs:
                        # Special handling for PENDING_FILES_SCAN
                        if directory == 'PENDING_FILES_SCAN':
                            # Delete the PENDING_FILES chunk (without _SCAN suffix)
                            deleted = db.session.query(ScanChunk).filter(
                                ScanChunk.directory_path == 'PENDING_FILES'
                            ).delete(synchronize_session=False)
                            logger.info(f"Deleted {deleted} PENDING_FILES chunks")
                        else:
                            # Delete chunks for this directory and all subdirectories
                            db.session.query(ScanChunk).filter(
                                ScanChunk.directory_path.like(f"{directory}%")
                            ).delete(synchronize_session=False)
                    db.session.commit()
                    logger.info("Old scan chunks cleaned up successfully")
                    
                    excluded_paths, excluded_extensions, excluded_patterns = load_exclusions_with_patterns()
                    checker = PixelProbe(
                        database_path=self.database_uri,
                        excluded_paths=excluded_paths,
                        excluded_extensions=excluded_extensions,
                        excluded_patterns=excluded_patterns
                    )
                    
                    # Create progress tracker for scan operations
                    progress_tracker = ProgressTracker('scan')
                    
                    # Import ScanResult model (needed for both regular and pending scans)
                    from models import ScanResult
                    
                    # Log scan start
                    logger.info(f"=== SCAN STARTED ===")
                    logger.info(f"Scan ID: {scan_state.scan_id}")
                    logger.info(f"Directories: {valid_dirs}")
                    logger.info(f"Force rescan: {force_rescan}")
                    logger.info(f"Workers: {num_workers}")
                    
                    # Check if this is a pending files scan
                    is_pending_scan = valid_dirs == ['PENDING_FILES_SCAN']
                    
                    if is_pending_scan:
                        # Skip discovery and adding phases for pending scan
                        logger.info("=== PENDING FILES SCAN ===")
                        logger.info("Skipping discovery and adding phases - scanning all pending files")
                        all_files = []
                        new_files_count = 0
                    else:
                        # Phase 1: Discovery - Find only new files
                        self.update_progress(0, 0, '', 'discovering')
                        scan_state.update_progress(0, 0, phase='discovering')
                        scan_state.phase_number = 1
                        scan_state.phase_current = 0
                        scan_state.phase_total = 0  # Unknown during discovery
                        scan_state.progress_message = 'Phase 1 of 3: Discovering media files...'
                        db.session.commit()
                        db.session.flush()
                        # Force the state to be visible to other sessions
                        db.session.expire(scan_state)
                        
                        if self.scan_cancelled:
                            self._handle_scan_cancellation(scan_state)
                            return
                    
                    if not is_pending_scan:
                        # Get count of existing files for logging
                        existing_count = db.session.query(ScanResult).count()
                        logger.info(f"Database contains {existing_count} existing files")
                        
                        # Also check how many are in completed state
                        completed_count = db.session.query(ScanResult).filter(
                            ScanResult.scan_status == 'completed'
                        ).count()
                        logger.info(f"Database has {completed_count} completed scans out of {existing_count} total files")
                        
                        logger.info("Starting file discovery with efficient batch database filtering...")
                        
                        # Instead of loading all paths into memory, we'll use batch checking
                        # This callback will be used by the discovery process
                        # IMPORTANT: This may be called from Celery context, so we need to handle app context
                        
                        # Create a persistent connection for Celery context (reused across batches)
                        celery_engine = None
                        celery_session_maker = None
                        
                        def check_files_exist_batch(file_paths):
                            """Check which files exist in database using batch query"""
                            nonlocal celery_engine, celery_session_maker
                            
                            if not file_paths:
                                return set()
                            
                            # Query database for these specific paths
                            try:
                                # Check if we're in a Flask app context
                                from flask import has_app_context
                                
                                if has_app_context():
                                    # We're in Flask context, use db.session directly
                                    existing = db.session.query(ScanResult.file_path).filter(
                                        ScanResult.file_path.in_(file_paths)
                                    ).all()
                                    existing_set = set(row[0] for row in existing)
                                else:
                                    # We're in Celery context, need our own connection
                                    from sqlalchemy import create_engine, text
                                    from sqlalchemy.orm import sessionmaker
                                    
                                    # Create engine once and reuse it
                                    if celery_engine is None:
                                        logger.info(f"Creating database engine for Celery batch checks: {self.database_uri}")
                                        celery_engine = create_engine(
                                            self.database_uri,
                                            pool_size=5,
                                            max_overflow=10,
                                            pool_pre_ping=True,
                                            pool_recycle=3600
                                        )
                                        celery_session_maker = sessionmaker(bind=celery_engine)
                                    
                                    session = celery_session_maker()
                                    try:
                                        # Use simpler query with text() to avoid needing ScanResult model in Celery
                                        # Break into smaller chunks to avoid parameter limits
                                        existing_set = set()
                                        chunk_size = 500  # PostgreSQL can handle this many parameters easily
                                        
                                        for i in range(0, len(file_paths), chunk_size):
                                            chunk = file_paths[i:i + chunk_size]
                                            # Use tuple parameter binding which is more efficient
                                            query = text("SELECT file_path FROM scan_results WHERE file_path = ANY(:paths)")
                                            result = session.execute(query, {'paths': chunk})
                                            existing_set.update(row[0] for row in result)
                                    finally:
                                        session.close()
                                
                                # Log the first check to verify it's working
                                if not hasattr(check_files_exist_batch, 'logged'):
                                    check_files_exist_batch.logged = True
                                    logger.info(f"Batch check working: {len(file_paths)} paths checked, {len(existing_set)} found in DB")
                                
                                return existing_set
                            except Exception as e:
                                logger.error(f"Database query failed in batch check: {e}")
                                import traceback
                                logger.error(f"Traceback: {traceback.format_exc()}")
                                # Return empty set on error to avoid blocking discovery
                                return set()
                        
                        # Define progress callback for discovery
                        def discovery_progress(files_checked, files_discovered):
                            self.update_progress(files_checked, files_checked, '', 'discovering')
                            scan_state.update_progress(files_checked, files_checked, phase='discovering', current_file='')
                            scan_state.discovery_count = files_discovered
                            db.session.commit()
                        
                        # Discover only new files (not already in database)
                        # Pass the batch check function instead of a huge in-memory set
                        all_files = checker.discover_media_files(valid_dirs, batch_check_callback=check_files_exist_batch, progress_callback=discovery_progress)
                        logger.info(f"File discovery completed. Found {len(all_files)} new files to add (database had {existing_count} existing files)")
                        
                        # Clean up Celery engine if it was created
                        if celery_engine is not None:
                            logger.info("Disposing of Celery database engine after discovery")
                            celery_engine.dispose()
                            celery_engine = None
                            celery_session_maker = None
                        
                        # SMART PRIORITIZATION: Sort files by modification time (newest first)
                        # This ensures recently added/modified files are processed first
                        if all_files:
                            logger.info("Sorting files by modification time (newest first)...")
                            import os
                            files_with_mtime = []
                            for filepath in all_files:
                                try:
                                    mtime = os.path.getmtime(filepath)
                                    files_with_mtime.append((filepath, mtime))
                                except OSError:
                                    # If we can't get mtime, add with timestamp 0 (process last)
                                    files_with_mtime.append((filepath, 0))
                            
                            # Sort by mtime descending (newest first)
                            files_with_mtime.sort(key=lambda x: x[1], reverse=True)
                            all_files = [f[0] for f in files_with_mtime]
                            
                            # Log info about the files being processed
                            if files_with_mtime:
                                newest = datetime.fromtimestamp(files_with_mtime[0][1], tz=timezone.utc)
                                oldest = datetime.fromtimestamp(files_with_mtime[-1][1], tz=timezone.utc) if files_with_mtime[-1][1] > 0 else None
                                logger.info(f"Files sorted - Newest: {newest}, Oldest: {oldest}")
                        
                        new_files_count = len(all_files)
                    
                    if self.scan_cancelled:
                        self._handle_scan_cancellation(scan_state)
                        return
                    
                    # Phase 2: Adding - Add new files to database with basic info
                    if new_files_count > 0:
                        self.update_progress(0, new_files_count, '', 'adding')
                        scan_state.update_progress(0, new_files_count, phase='adding')
                        scan_state.phase_number = 2
                        scan_state.phase_current = 0
                        scan_state.phase_total = new_files_count  # Fix: Set phase_total for UI display
                        scan_state.progress_message = f'Phase 2 of 3: Adding {new_files_count} new files to database...'
                        db.session.commit()
                        db.session.flush()
                        # Force the state to be visible to other sessions
                        db.session.expire(scan_state)
                        
                        # Add new files to database with basic file info (no corruption check yet)
                        added_count = 0
                        duplicate_count = 0
                        batch_size = 100  # Smaller batch size to prevent database connection issues
                        
                        # Process files in batches for better performance
                        for batch_start in range(0, len(all_files), batch_size):
                            # Check for cancellation both locally and in database
                            if self.scan_cancelled:
                                self._handle_scan_cancellation(scan_state)
                                return
                            
                            # Check scan state for cancellation
                            if scan_state.phase == 'cancelled':
                                logger.info("Scan cancelled - stopping scan")
                                self.scan_cancelled = True
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
                            
                            # Update scan state with files added
                            # Safely set files_added if column exists
                            if hasattr(scan_state, 'files_added'):
                                scan_state.files_added = added_count
                            
                            # Update progress with error handling to prevent thread death
                            try:
                                self.update_progress(batch_end, new_files_count, batch_files[-1] if batch_files else '', 'adding')
                                scan_state.update_progress(batch_end, new_files_count, current_file=batch_files[-1] if batch_files else '')
                            except Exception as progress_error:
                                logger.error(f"Error updating progress for batch {batch_start}-{batch_end}: {progress_error}")
                                logger.error(f"Progress error traceback: {traceback.format_exc()}")
                                # Re-attach scan_state if detached
                                try:
                                    _ = scan_state.id
                                except Exception:
                                    scan_state = db.session.merge(scan_state)
                                    logger.info("Re-attached detached scan_state during progress update")
                            
                            # Note: Commit is now done inside _add_files_batch_to_db
                            logger.info(f"Processed batch {batch_start//batch_size + 1}/{(len(all_files) + batch_size - 1)//batch_size}: Added {added_count} total, {duplicate_count} duplicates (batch end: {batch_end}/{len(all_files)})")
                            
                            # Safety check: Only abort if we're in an infinite loop situation
                            # This happens when discovery keeps finding the same files repeatedly
                            # But it's normal for all files to be duplicates if they already exist in DB
                            # We should only abort if we're processing way more files than discovered
                            total_processed = added_count + duplicate_count
                            if total_processed > new_files_count * 2 and new_files_count > 0:
                                logger.error(f"Processing more files than discovered ({total_processed} > {new_files_count * 2}). Possible infinite loop.")
                                logger.error("Aborting add phase to prevent infinite loop.")
                                break
                            
                            # Log high duplicate rate but don't abort - it's normal for existing files
                            if total_processed > 1000 and duplicate_count == total_processed:
                                logger.info(f"All {duplicate_count} files already exist in database. This is normal for re-discovered files.")
                            
                            # More frequent commits for Celery tasks to save progress
                            # Commit every 10,000 files to ensure progress is saved if task is killed
                            if batch_end % 10000 == 0:
                                logger.info(f"Checkpoint at {batch_end} files - committing transaction")
                                db.session.commit()
                                # Simple garbage collection without aggressive session cleanup
                                import gc
                                gc.collect()
                        
                        db.session.commit()
                        logger.info(f"Add phase completed. Added {added_count} new files out of {new_files_count} discovered")
                    
                    # Phase 3: Scanning - Check integrity of files that need scanning
                    # First count total files to scan
                    if is_pending_scan:
                        # For pending scan, get ALL pending files regardless of directory
                        total_scan_files = db.session.query(ScanResult).filter(
                            ScanResult.scan_status == 'pending'
                        ).count()
                        logger.info(f"Pending files scan: found {total_scan_files} pending files to scan")
                    elif force_rescan:
                        total_scan_files = db.session.query(ScanResult).filter(
                            db.or_(*[ScanResult.file_path.like(f"{d}%") for d in valid_dirs])
                        ).count()
                    else:
                        # Normal scan: count NEW and PENDING files for corruption check
                        # This includes newly discovered files (pending status)
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
                            if force_rescan:
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
                    # For Phase 3 scanning, create file-based chunks, not directory-based!
                    scan_chunks = self._create_scanning_chunks(total_scan_files, scan_state.scan_id, is_pending_scan, force_rescan, valid_dirs)
                    
                    # Save chunks to database
                    for chunk in scan_chunks:
                        chunk.phase = 'scanning'
                        db.session.add(chunk)
                    db.session.commit()
                    
                    logger.info(f"Starting scan phase: {total_scan_files} files to scan across {len(scan_chunks)} chunks")
                    
                    # Update both service and database state for actual scanning
                    self.update_progress(0, total_scan_files, '', 'scanning')
                    scan_state.update_progress(0, total_scan_files, phase='scanning', current_file='')
                    scan_state.phase_number = 3
                    scan_state.phase_current = 0
                    scan_state.phase_total = total_scan_files  # Fix: Set phase_total for UI display
                    scan_state.progress_message = f'Phase 3 of 3: Scanning {total_scan_files} files for corruption...'
                    
                    # Explicit commit to ensure database state is updated
                    db.session.commit()
                    # Force flush to ensure changes are written immediately
                    db.session.flush()
                    logger.info(f"Scan state transitioned to 'scanning' phase "
                               f"with {total_scan_files} files")
                    
                    if num_workers > 1:
                        self._parallel_scan_chunks(checker, scan_chunks, force_rescan, num_workers, scan_state, scan_state_id)
                    else:
                        self._sequential_scan_chunks(checker, scan_chunks, force_rescan, scan_state, scan_state_id)
                        
                except Exception as e:
                    logger.error(f"=== SCAN ERROR ===")
                    logger.error(f"Scan ID: {scan_state_id}")
                    # Safely get phase without risking DetachedInstanceError that kills the thread
                    try:
                        phase = scan_state.phase if scan_state else 'unknown'
                    except Exception:
                        phase = 'unknown (detached)'
                    logger.error(f"Phase at error: {phase}")
                    logger.error(f"Error: {e}")
                    logger.error(f"Error type: {type(e).__name__}")
                    logger.error(f"=== END SCAN ERROR ===")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    try:
                        self.update_progress(0, 0, '', 'error')
                    except Exception as progress_error:
                        logger.error(f"Failed to update progress on error: {progress_error}")
                    
                    # Enhanced crash recovery tracking
                    try:
                        if scan_state:
                            # Re-attach scan_state if it's detached to prevent secondary errors
                            try:
                                # Test if scan_state is attached by accessing a property
                                _ = scan_state.id
                            except Exception:
                                # Re-attach the detached instance
                                scan_state = db.session.merge(scan_state)
                                logger.info("Re-attached detached scan_state for crash recovery")
                            
                            # Update crash info (temporarily without new columns until migration completes)
                            scan_state.error_message = str(e)[:1000]  # Truncate to avoid VARCHAR limit
                            
                            # Mark scan as crashed instead of just error
                            scan_state.is_active = False
                            scan_state.phase = 'crashed'
                            scan_state.end_time = datetime.now(timezone.utc)
                            
                            db.session.commit()
                            logger.info(f"Scan marked as crashed")
                    except Exception as recovery_error:
                        logger.error(f"Failed to update crash recovery info: {recovery_error}")
                    
                    raise
                finally:
                    # Clear thread reference to allow new scans
                    self.current_scan_thread = None
                    logger.info("Scan thread cleaned up")
        
        if async_mode:
            # Run in a separate thread (for direct API calls)
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
        else:
            # Run synchronously (for Celery tasks)
            logger.info("Running scan synchronously for Celery task")
            try:
                run_scan()
                # Get final scan state for results
                final_scan_state = ScanState.query.get(scan_state_id)
                if final_scan_state:
                    # Get corrupted file count from ScanResult table
                    # Note: ScanResult doesn't have scan_id, so we query all corrupted files
                    from models import ScanResult
                    corrupted_found = db.session.query(ScanResult).filter_by(
                        is_corrupted=True
                    ).count()
                    return {
                        'status': 'completed',
                        'message': 'Scan completed',
                        'directories': valid_dirs,
                        'force_rescan': force_rescan,
                        'num_workers': num_workers,
                        'files_processed': final_scan_state.files_processed or 0,
                        'files_discovered': final_scan_state.discovery_count or 0,
                        'corrupted_found': corrupted_found,
                        'phase': final_scan_state.phase
                    }
                else:
                    return {
                        'status': 'completed',
                        'message': 'Scan completed',
                        'directories': valid_dirs,
                        'force_rescan': force_rescan,
                        'num_workers': num_workers
                    }
            finally:
                # Ensure thread reference is cleared even in sync mode
                self.current_scan_thread = None
    
    def scan_files(self, file_paths: List[str], force_rescan: bool = False,
                   num_workers: int = 1, async_mode: bool = True) -> Dict:
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
        # Safely set num_workers if column exists
        if hasattr(scan_state, 'num_workers'):
            scan_state.num_workers = num_workers  # Track the number of workers used
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
                    scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                    if not scan_state:
                        logger.error(f"Could not find scan state with ID {scan_state_id}")
                        return
                    
                    excluded_paths, excluded_extensions, excluded_patterns = load_exclusions_with_patterns()
                    checker = PixelProbe(
                        database_path=self.database_uri,
                        excluded_paths=excluded_paths,
                        excluded_extensions=excluded_extensions,
                        excluded_patterns=excluded_patterns
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
                            # Add timestamp to ensure uniqueness
                            timestamp = time.time()
                            chunk_id = hashlib.md5(f"{scan_state.scan_id}:{dir_path}:{timestamp}".encode()).hexdigest()
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
                        # Truncate message to avoid VARCHAR limit
                        scan_state.progress_message = f'Scanning {total_files} files in {len(chunks)} dirs'[:200]
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
        
        if async_mode:
            # Run in a separate thread (for direct API calls)
            self.current_scan_thread = threading.Thread(target=run_scan, name="FileListScan")
            logger.info(f"Starting file list scan thread: {self.current_scan_thread.name}")
            self.current_scan_thread.start()
            
            return {
                'status': 'started',
                'message': f'Scan started for {len(valid_files)} files',
                'files': len(valid_files),
                'force_rescan': force_rescan,
                'num_workers': num_workers
            }
        else:
            # Run synchronously (for Celery tasks)
            logger.info("Running file scan synchronously for Celery task")
            try:
                run_scan()
                # Get final scan state for results
                final_scan_state = ScanState.query.get(scan_state_id)
                if final_scan_state:
                    # Get corrupted file count from ScanResult table
                    # Note: ScanResult doesn't have scan_id, so we query all corrupted files
                    from models import ScanResult
                    corrupted_found = db.session.query(ScanResult).filter_by(
                        is_corrupted=True
                    ).count()
                    return {
                        'status': 'completed',
                        'message': f'Scan completed for {len(valid_files)} files',
                        'files': len(valid_files),
                        'force_rescan': force_rescan,
                        'num_workers': num_workers,
                        'files_processed': final_scan_state.files_processed or 0,
                        'files_scanned': final_scan_state.files_processed or 0,  # For compatibility
                        'files_discovered': final_scan_state.discovery_count or 0,
                        'corrupted_found': corrupted_found,
                        'phase': final_scan_state.phase
                    }
                else:
                    return {
                        'status': 'completed',
                        'message': f'Scan completed for {len(valid_files)} files',
                        'files': len(valid_files),
                        'force_rescan': force_rescan,
                                'num_workers': num_workers
                    }
            finally:
                # Ensure thread reference is cleared even in sync mode
                self.current_scan_thread = None
    
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
                    scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                    if not scan_state:
                        logger.error(f"Could not find scan state {scan_state_id}")
                        return
                    
                    # Initialize checker
                    excluded_paths, excluded_extensions, excluded_patterns = load_exclusions_with_patterns()
                    checker = PixelProbe(
                        database_path=self.database_uri,
                        excluded_paths=excluded_paths,
                        excluded_extensions=excluded_extensions,
                        excluded_patterns=excluded_patterns
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
                    
                    # Create scan report for resumed scan
                    self._create_scan_report(scan_state, scan_type='resume_scan')
                    
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
        """Cancel the current scan - nuclear option: kill everything"""
        logger.info("cancel_scan() method called - NUCLEAR OPTION")
        
        # Get current scan state
        scan_state = ScanState.get_or_create()
        
        logger.info(f"Cancel scan - scan_id: {scan_state.scan_id}, phase: {scan_state.phase}")
        
        # Step 1: Kill ALL Celery tasks (nuclear option)
        try:
            from celery_config import celery_app
            
            logger.info("Step 1: Killing ALL Celery tasks")
            
            # Get inspection object
            inspect = celery_app.control.inspect()
            
            # Kill ALL active tasks on ALL workers
            active = inspect.active()
            if active:
                task_count = 0
                for worker_name, tasks in active.items():
                    logger.info(f"Killing {len(tasks)} tasks on worker {worker_name}")
                    for task in tasks:
                        task_id = task.get('id')
                        celery_app.control.revoke(task_id, terminate=True, signal='SIGKILL')
                        task_count += 1
                logger.info(f"Killed {task_count} active tasks")
            
            # Revoke ALL reserved/queued tasks
            reserved = inspect.reserved()
            if reserved:
                task_count = 0
                for worker_name, tasks in reserved.items():
                    logger.info(f"Revoking {len(tasks)} reserved tasks on worker {worker_name}")
                    for task in tasks:
                        task_id = task.get('id')
                        celery_app.control.revoke(task_id, terminate=False)
                        task_count += 1
                logger.info(f"Revoked {task_count} reserved tasks")
            
            # Purge the entire queue
            celery_app.control.purge()
            logger.info("Purged entire Celery queue")
            
        except Exception as e:
            logger.error(f"Error killing Celery tasks: {e}")
        
        # Step 2: Clean up database state
        logger.info("Step 2: Cleaning up database state")
        
        try:
            from models import ScanChunk
            
            # Mark ALL chunks as cancelled
            chunks_updated = db.session.query(ScanChunk).filter(
                ScanChunk.scan_id == scan_state.scan_id,
                ScanChunk.status.in_(['pending', 'processing', 'queued'])
            ).update({
                'status': 'cancelled',
                'end_time': datetime.now(timezone.utc)
            }, synchronize_session=False)
            
            logger.info(f"Marked {chunks_updated} chunks as cancelled")
            
            # Reset any files stuck in 'scanning' status
            files_reset = db.session.query(ScanResult).filter_by(
                scan_status='scanning'
            ).update({
                'scan_status': 'pending'
            }, synchronize_session=False)
            
            logger.info(f"Reset {files_reset} files from 'scanning' to 'pending'")
        
            # Cancel the scan state
            scan_state.cancel_scan()
            
            # Commit all changes
            db.session.commit()
            
        except Exception as e:
            logger.error(f"Error cleaning up database: {e}")
        
        # Step 3: Set cancellation flag and update progress
        self.scan_cancelled = True
        logger.info("Step 3: Set cancellation flag")
        
        # Force progress update to show cancelled state
        self.update_progress(
            self.scan_progress.get('current', 0),
            self.scan_progress.get('total', 0),
            '',
            'cancelled'
        )
        
        # Force thread reference cleanup
        if self.current_scan_thread is not None:
            logger.info("Cleaning up scan thread reference")
            self.current_scan_thread = None
        
        logger.info("=== SCAN CANCELLATION COMPLETE (NUCLEAR) ===")
        
        return {
            'message': 'Scan cancellation completed - all tasks killed',
            'tasks_killed': True,
            'database_cleaned': True
        }
    
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
        total_files_to_scan = scan_state.phase_total  # Initial estimate from scanning phase
        actual_total_discovered = 0  # Track actual total as we discover files in chunks
        chunks_processed = 0
        
        # Create progress tracker for scan
        progress_tracker = ProgressTracker('scan')
        
        for i, chunk in enumerate(chunks):
            if self.scan_cancelled:
                break
            
            logger.info(f"Processing chunk {i+1}/{total_chunks}: {chunk.directory_path}")
            
            # Get initial files scanned count for this chunk
            initial_scanned = total_files_scanned
            
            # Scan files in this chunk - First we need to know how many files are in this chunk
            # to update our total estimate
            chunk_files_count = self._get_chunk_file_count(chunk, force_rescan)
            actual_total_discovered += chunk_files_count
            
            # Update total estimate based on discovered files and remaining chunks
            # Use the actual discovered count plus an estimate for remaining chunks
            remaining_chunks = total_chunks - chunks_processed - 1
            if chunks_processed > 0 and remaining_chunks > 0:
                avg_files_per_chunk = actual_total_discovered / (chunks_processed + 1)
                estimated_remaining = int(avg_files_per_chunk * remaining_chunks)
                total_files_to_scan = actual_total_discovered + estimated_remaining
            else:
                phase_total = getattr(scan_state, 'phase_total', 0) or 0
                total_files_to_scan = max(actual_total_discovered, phase_total)
            
            # Now scan the chunk with updated total
            self._scan_chunk_files(chunk, checker, force_rescan, total_files_scanned, total_files_to_scan, scan_state)
            
            chunks_processed += 1
            
            # Update total files scanned based on chunk results
            if chunk.files_scanned:
                total_files_scanned += chunk.files_scanned
            
            # Update progress with actual file counts
            self.update_progress(total_files_scanned, total_files_to_scan, chunk.directory_path, 'scanning')
            
            # Update scan state progress with files, not chunks (with error recovery)
            try:
                scan_state.current_chunk_index = i + 1
                scan_state.files_processed = total_files_scanned  # Ensure files_processed is set
                # Don't update estimated_total during scanning - it should be locked after discovery
                # scan_state.estimated_total = total_files_to_scan  # REMOVED - causes confusing UI
                scan_state.update_progress(total_files_scanned, total_files_to_scan, current_file='')
                
                # Update progress message
                scan_state.progress_message = progress_tracker.get_progress_message(
                    f'Phase 3 of 3: Scanning files across {total_chunks} directories',
                    total_files_scanned,
                    total_files_to_scan,
                    os.path.basename(chunk.directory_path)
                )
                # Force commit and flush to ensure visibility
                db.session.commit()
                db.session.flush()
                # For Celery, also expire the object to force re-read
                db.session.expire(scan_state)
            except Exception as e:
                logger.error(f"Failed to update progress for chunk {chunk.directory_path}: {e}")
                # Try to recover the database session
                try:
                    db.session.rollback()
                    # Re-get scan state and try again
                    scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                    if scan_state:
                        scan_state.update_progress(total_files_scanned, total_files_to_scan, current_file='')
                        db.session.commit()
                except Exception as e2:
                    logger.error(f"Failed to recover progress update: {e2}")
            
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
                scan_type = 'rescan' if force_rescan else 'full_scan'
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
            
            # Update scan state progress with error recovery
            try:
                scan_state.update_progress(i + 1, total_files, current_file=file_path)
                
                # Update progress message with current file and ETA
                scan_state.progress_message = progress_tracker.get_progress_message(
                    f'Phase 3 of 3: Scanning {total_files} files for corruption',
                    i + 1,
                    total_files,
                    os.path.basename(file_path)
                )
                db.session.commit()
            except Exception as e:
                logger.error(f"Failed to update progress for file {file_path}: {e}")
                # Try to recover the database session
                try:
                    db.session.rollback()
                    # Re-get scan state and try again
                    scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                    if scan_state:
                        scan_state.update_progress(i + 1, total_files, current_file=file_path)
                        db.session.commit()
                except Exception as e2:
                    logger.error(f"Failed to recover progress update: {e2}")
            
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
                if force_rescan:
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
        total_files_to_scan = scan_state.phase_total  # Initial estimate from scanning phase
        actual_total_discovered = 0  # Track actual total as we discover files in chunks
        files_scanned_lock = threading.Lock()
        discovery_lock = threading.Lock()
        
        # Create progress tracker for scan
        progress_tracker = ProgressTracker('scan')

        # Capture Flask app for worker threads
        from flask import current_app
        app = current_app._get_current_object()

        def scan_chunk(chunk):
            # Set up Flask app context for worker thread
            with app.app_context():
                if self.scan_cancelled:
                    return None
                # For parallel scan, we can't pass cumulative counts, so pass 0
                # The main thread will handle updating the cumulative progress
                # Pass num_workers so files within each chunk are scanned in parallel
                self._scan_chunk_files(chunk, checker, force_rescan, 0, 0, scan_state, num_workers=num_workers)
                return chunk, chunk.files_scanned or 0
        
        with ThreadPoolExecutor(max_workers=min(num_workers, len(chunks))) as executor:
            # First, get file counts for all chunks to get accurate total
            logger.info("Calculating total files to scan across all chunks...")
            chunk_counts = {}
            for chunk in chunks:
                count = self._get_chunk_file_count(chunk, force_rescan)
                chunk_counts[chunk.chunk_id] = count
                actual_total_discovered += count
            
            # Update total with actual count
            total_files_to_scan = actual_total_discovered
            scan_state.estimated_total = total_files_to_scan
            scan_state.update_progress(0, total_files_to_scan, phase='scanning')
            logger.info(f"Total files to scan: {total_files_to_scan} across {total_chunks} chunks")
            
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
                    
                    # Update scan state progress with file counts (with error recovery)
                    try:
                        scan_state.current_chunk_index = completed_chunks
                        scan_state.files_processed = current_files_scanned  # Ensure files_processed is set
                        scan_state.update_progress(current_files_scanned, total_files_to_scan, current_file='')
                        
                        # Update progress message
                        scan_state.progress_message = progress_tracker.get_progress_message(
                            f'Phase 3 of 3: Scanning files across {total_chunks} directories (parallel)',
                            current_files_scanned,
                            total_files_to_scan,
                            os.path.basename(chunk.directory_path)
                        )
                        # Force commit and flush to ensure visibility
                        db.session.commit()
                        db.session.flush()
                        # For Celery, also expire the object to force re-read
                        db.session.expire(scan_state)
                    except Exception as e:
                        logger.error(f"Failed to update progress for chunk {chunk.directory_path}: {e}")
                        # Try to recover the database session
                        try:
                            db.session.rollback()
                            # Re-get scan state and try again
                            scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                            if scan_state:
                                scan_state.update_progress(current_files_scanned, total_files_to_scan, current_file='')
                                db.session.commit()
                        except Exception as e2:
                            logger.error(f"Failed to recover progress update: {e2}")
                    
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
                scan_type = 'rescan' if force_rescan else 'full_scan'
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
                try:
                    scan_state.update_progress(completed, total_files, current_file=file_path)
                    
                    # Update progress message with current file and ETA
                    scan_state.progress_message = progress_tracker.get_progress_message(
                        f'Phase 3 of 3: Scanning {total_files} files for corruption',
                        completed,
                        total_files,
                        os.path.basename(file_path)
                    )
                    db.session.commit()
                except Exception as e:
                    logger.error(f"Failed to update progress for file {file_path}: {e}")
                    # Try to recover the database session
                    try:
                        db.session.rollback()
                        # Re-get scan state and try again
                        scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                        if scan_state:
                            scan_state.update_progress(completed, total_files, current_file=file_path)
                            db.session.commit()
                    except Exception as e2:
                        logger.error(f"Failed to recover progress update: {e2}")
                
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
                if force_rescan:
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
                num_workers=scan_state.num_workers if hasattr(scan_state, 'num_workers') else 1,
                total_files_discovered=scan_state.estimated_total,
                files_scanned=stats.completed or 0,
                files_added=scan_state.files_added if hasattr(scan_state, 'files_added') else 0,
                files_updated=scan_state.files_updated if hasattr(scan_state, 'files_updated') else 0,
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
                    'is_corrupted': None,
                    'marked_as_good': False,
                    'file_exists': True,
                    'has_warnings': False,
                    'deep_scan': False  # Temporary until migration runs
                })
                
            except Exception as e:
                logger.error(f"Failed to get file info: {file_path} - {e}")
                # Create minimal entry for files with errors
                files_to_insert.append({
                    'file_path': file_path,
                    'discovered_date': datetime.now(timezone.utc),
                    'scan_status': 'error',
                    'error_message': str(e),
                    'is_corrupted': None,
                    'marked_as_good': False,
                    'file_exists': True,
                    'has_warnings': False,
                    'deep_scan': False,  # Temporary until migration runs
                    'file_size': 0,  # Default for files with errors
                    'file_type': 'unknown',  # Default MIME type
                    'last_modified': datetime.now(timezone.utc)  # Use current time as fallback
                })
        
        # Bulk insert with duplicate handling
        if files_to_insert:
            try:
                # PostgreSQL bulk insert with ON CONFLICT handling
                from sqlalchemy.dialects.postgresql import insert
                stmt = insert(ScanResult).values(files_to_insert)
                stmt = stmt.on_conflict_do_nothing(index_elements=['file_path'])
                
                # Get count before insert
                file_paths_to_insert = [f['file_path'] for f in files_to_insert]
                existing_before = db.session.query(ScanResult.file_path).filter(
                    ScanResult.file_path.in_(file_paths_to_insert)
                ).count()
                
                # Execute bulk insert
                db.session.execute(stmt)
                db.session.commit()
                
                # Calculate actual added
                existing_after = db.session.query(ScanResult.file_path).filter(
                    ScanResult.file_path.in_(file_paths_to_insert)
                ).count()
                
                actual_added = existing_after - existing_before
                added_count += actual_added
                duplicate_count = len(files_to_insert) - actual_added
                
                logger.info(f"Batch insert: attempted {len(files_to_insert)}, actually added {actual_added}, duplicates {duplicate_count}")
                            
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
    
    def _create_scanning_chunks(self, total_files: int, scan_id: str, is_pending_scan: bool, 
                                force_rescan: bool, directories: List[str]) -> List[ScanChunk]:
        """Create file-based chunks for Phase 3 scanning"""
        from models import ScanChunk
        chunks = []
        
        # Determine optimal chunk size based on total files
        if total_files <= 100:
            chunk_size = total_files  # Single chunk for small scans
        elif total_files <= 1000:
            chunk_size = 100  # 100 files per chunk
        elif total_files <= 10000:
            chunk_size = 500  # 500 files per chunk
        else:
            chunk_size = 1000  # 1000 files per chunk for large scans
        
        num_chunks = (total_files + chunk_size - 1) // chunk_size
        logger.info(f"Creating {num_chunks} file-based chunks for {total_files} files (chunk size: {chunk_size})")
        
        # Create file-based chunks
        for i in range(num_chunks):
            chunk_id = hashlib.md5(f"{scan_id}:scan_chunk_{i}:{time.time()}".encode()).hexdigest()
            
            # Store chunk metadata in directory_path for now (will be refactored later)
            # Format: "FILE_CHUNK:offset:limit"
            offset = i * chunk_size
            limit = min(chunk_size, total_files - offset)
            
            chunk = ScanChunk(
                scan_id=scan_id,
                chunk_id=chunk_id,
                directory_path=f"FILE_CHUNK:{offset}:{limit}",
                phase='scanning',
                status='pending',
                files_discovered=limit  # Set the expected file count
            )
            chunks.append(chunk)
        
        return chunks
    
    def _create_directory_chunks(self, directories: List[str], scan_id: str) -> List[ScanChunk]:
        """Create chunks based on directory structure for better organization"""
        chunks = []
        
        # Check for special pending files scan
        if directories == ['PENDING_FILES_SCAN']:
            # Create a single chunk for all pending files
            # Include timestamp to ensure unique chunk_id even if scan_id is reused
            # Use current time with microseconds for uniqueness
            timestamp = str(time.time())
            unique_id = f"{scan_id}:PENDING_FILES:{timestamp}:{datetime.now(timezone.utc).isoformat()}"
            chunk_id = hashlib.md5(unique_id.encode()).hexdigest()
            chunk = ScanChunk(
                scan_id=scan_id,
                chunk_id=chunk_id,
                directory_path='PENDING_FILES',
                phase='pending',
                status='pending'
            )
            return [chunk]
        
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
            # Add timestamp for uniqueness
            timestamp = time.time()
            chunk_id = hashlib.md5(f"{scan_id}:{dir_path}:{timestamp}".encode()).hexdigest()
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
                # Check if this is a file-based chunk (Phase 3)
                if chunk.directory_path.startswith('FILE_CHUNK:'):
                    # Parse the chunk metadata: "FILE_CHUNK:offset:limit"
                    parts = chunk.directory_path.split(':')
                    offset = int(parts[1])
                    limit = int(parts[2])
                    
                    # Get the specified range of pending files
                    files_to_scan = db.session.query(ScanResult).filter(
                        ScanResult.scan_status == 'pending'
                    ).order_by(ScanResult.file_path).offset(offset).limit(limit).all()
                    
                    logger.info(f"Processing FILE_CHUNK {chunk.chunk_id}: {len(files_to_scan)} files (offset={offset}, limit={limit})")
                elif chunk.directory_path == 'PENDING_FILES':
                    # Legacy: Scan ALL pending files regardless of directory
                    files_to_scan = db.session.query(ScanResult).filter(
                        ScanResult.scan_status == 'pending'
                    ).all()
                    logger.info(f"Processing PENDING_FILES chunk: {len(files_to_scan)} pending files to scan")
                else:
                    # Legacy: Scan files in this directory
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
    
    def _get_chunk_file_count(self, chunk: ScanChunk, force_rescan: bool) -> int:
        """Get count of files to scan in a chunk without actually scanning them"""
        # Check if this is the special pending files chunk
        if chunk.directory_path == 'PENDING_FILES':
            # Count ALL pending files regardless of directory
            return db.session.query(ScanResult).filter(
                ScanResult.scan_status == 'pending'
            ).count()
        
        chunk_dir = chunk.directory_path.rstrip(os.sep)
        
        if force_rescan:
            # Count all files that start with this directory path
            count = db.session.query(ScanResult).filter(
                ScanResult.file_path.startswith(chunk_dir)
            ).count()
        else:
            # Count only pending files
            count = db.session.query(ScanResult).filter(
                db.and_(
                    ScanResult.file_path.startswith(chunk_dir),
                    ScanResult.scan_status == 'pending'
                )
            ).count()
        
        return count
    
    def _scan_chunk_files(self, chunk: ScanChunk, checker: PixelProbe, force_rescan: bool = False,
                          total_scanned_so_far: int = 0, total_to_scan: int = 0, scan_state: ScanState = None,
                          num_workers: int = 1):
        """Scan files in a chunk that are already in the database

        Args:
            num_workers: Number of parallel workers to use for scanning files within this chunk
        """
        chunk.status = 'processing'
        chunk.phase = 'scanning'
        chunk.start_time = datetime.now(timezone.utc)
        db.session.commit()
        
        try:
            # Check if this is a file-based chunk (Phase 3)
            if chunk.directory_path.startswith('FILE_CHUNK:'):
                # Parse the chunk metadata: "FILE_CHUNK:offset:limit"
                parts = chunk.directory_path.split(':')
                offset = int(parts[1])
                limit = int(parts[2])
                
                # Get count for this specific chunk
                files_count = limit  # We know exactly how many files are in this chunk
                logger.info(f"FILE_CHUNK {chunk.chunk_id}: Processing {files_count} files (offset={offset})")
            elif chunk.directory_path == 'PENDING_FILES':
                # Legacy: Get ALL pending files regardless of directory
                files_count = db.session.query(ScanResult).filter(
                    ScanResult.scan_status == 'pending'
                ).count()
                logger.info(f"PENDING_FILES chunk: Found {files_count} pending files to scan")
            else:
                # Legacy: Query for files in this chunk's directory that need scanning
                # Simply match all files that start with the directory path
                chunk_dir = chunk.directory_path.rstrip(os.sep)
                
                # Get count first to avoid loading all files into memory
                if force_rescan:
                    # Count all files that start with this directory path
                    files_count = db.session.query(ScanResult).filter(
                        ScanResult.file_path.startswith(chunk_dir)
                    ).count()
                else:
                    # For normal scans, only scan pending files (new/unscanned)
                    files_count = db.session.query(ScanResult).filter(
                        db.and_(
                            ScanResult.file_path.startswith(chunk_dir),
                            ScanResult.scan_status == 'pending'
                        )
                    ).count()
            
            logger.info(f"Chunk {chunk.chunk_id}: Found {files_count} files to scan in {chunk.directory_path}")
            
            scanned = 0
            errors = 0
            batch_size = 100  # Moderate batch size to balance memory and performance
            last_commit_count = 0  # Track when we last committed

            # Thread-local storage for PixelProbe instances (create ONCE per chunk, not per batch)
            # This must be outside the batch loop so it persists across batches
            import threading
            thread_local = threading.local() if num_workers > 1 else None

            # Process files in batches to avoid loading all into memory
            for batch_offset in range(0, files_count, batch_size):
                if self.scan_cancelled:
                    chunk.status = 'cancelled'
                    chunk.end_time = datetime.now(timezone.utc)
                    db.session.commit()
                    return

                # Get batch of files
                if chunk.directory_path.startswith('FILE_CHUNK:'):
                    # For file-based chunks, adjust offset based on chunk's offset
                    chunk_offset = int(chunk.directory_path.split(':')[1])
                    if force_rescan:
                        # Rescan ALL files, not just pending
                        files_batch = db.session.query(ScanResult).order_by(
                            ScanResult.file_path
                        ).offset(chunk_offset + batch_offset).limit(batch_size).all()
                    else:
                        # Normal scan: only pending files
                        files_batch = db.session.query(ScanResult).filter(
                            ScanResult.scan_status == 'pending'
                        ).order_by(ScanResult.file_path).offset(chunk_offset + batch_offset).limit(batch_size).all()
                elif chunk.directory_path == 'PENDING_FILES':
                    # Legacy: Get batch of ALL pending files
                    files_batch = db.session.query(ScanResult).filter(
                        ScanResult.scan_status == 'pending'
                    ).offset(batch_offset).limit(batch_size).all()
                elif force_rescan:
                    files_batch = db.session.query(ScanResult).filter(
                        ScanResult.file_path.startswith(chunk_dir)
                    ).offset(batch_offset).limit(batch_size).all()
                else:
                    # For normal scans, only get pending files (new/unscanned)
                    files_batch = db.session.query(ScanResult).filter(
                        db.and_(
                            ScanResult.file_path.startswith(chunk_dir),
                            ScanResult.scan_status == 'pending'
                        )
                    ).offset(batch_offset).limit(batch_size).all()
                
                if not files_batch:
                    break  # No more files

                # Use parallel processing if num_workers > 1
                if num_workers > 1:
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    from flask import current_app

                    # Capture Flask app for worker threads
                    app = current_app._get_current_object()
                    scanned_lock = threading.Lock()

                    # Get exclusions for creating thread-local PixelProbe instances
                    from media_checker import load_exclusions_with_patterns
                    excluded_paths, excluded_extensions, excluded_patterns = load_exclusions_with_patterns()

                    # Note: thread_local is now created ONCE per chunk (line 1941), not per batch

                    def scan_single_file(file_result):
                        """Scan a single file in a worker thread"""
                        with app.app_context():
                            if self.scan_cancelled:
                                return None
                            try:
                                # Get or create thread-local PixelProbe instance (one per thread, not per file)
                                # This avoids creating thousands of connections for thousands of files
                                if not hasattr(thread_local, 'checker'):
                                    from media_checker import PixelProbe
                                    thread_local.checker = PixelProbe(
                                        database_path=self.database_uri,
                                        excluded_paths=excluded_paths,
                                        excluded_extensions=excluded_extensions,
                                        excluded_patterns=excluded_patterns
                                    )
                                thread_local.checker.scan_file(file_result.file_path, force_rescan=force_rescan)
                                return file_result, True, None
                            except Exception as e:
                                return file_result, False, str(e)

                    # Process files in parallel
                    with ThreadPoolExecutor(max_workers=num_workers) as file_executor:
                        future_to_file = {file_executor.submit(scan_single_file, f): f for f in files_batch}

                        for future in as_completed(future_to_file):
                            if self.scan_cancelled:
                                chunk.status = 'cancelled'
                                chunk.end_time = datetime.now(timezone.utc)
                                db.session.commit()
                                return

                            result = future.result()
                            if result is None:
                                continue

                            file_result, success, error = result

                            with scanned_lock:
                                if success:
                                    scanned += 1
                                else:
                                    errors += 1
                                    logger.error(f"Error scanning {file_result.file_path}: {error}")

                                # Update progress (less frequently for parallel to reduce contention)
                                current_total = total_scanned_so_far + scanned

                                if scanned % 10 == 0 or scanned == 1:
                                    self.update_progress(current_total, total_to_scan,
                                                       file_result.file_path, 'scanning')

                                # Determine update threshold
                                if total_to_scan < 20:
                                    update_threshold = 1
                                elif total_to_scan < 100:
                                    update_threshold = 5
                                elif total_to_scan < 1000:
                                    update_threshold = 10
                                else:
                                    update_threshold = 50

                                if scan_state and (scanned - last_commit_count) >= update_threshold:
                                    try:
                                        scan_state.files_processed = current_total
                                        scan_state.update_progress(current_total, total_to_scan, current_file=file_result.file_path)

                                        from utils import ProgressTracker
                                        progress_tracker = ProgressTracker('scan')
                                        scan_state.progress_message = progress_tracker.get_progress_message(
                                            f'Phase 3 of 3: Scanning files (parallel: {num_workers} workers)',
                                            current_total,
                                            total_to_scan,
                                            os.path.basename(file_result.file_path)
                                        )
                                        db.session.commit()
                                        last_commit_count = scanned

                                        if scanned % 1000 == 0:
                                            import gc
                                            gc.collect()
                                    except Exception as e:
                                        logger.error(f"Failed to update progress: {e}")
                else:
                    # Sequential processing (original code)
                    for file_result in files_batch:
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

                            # Update progress every 10 files for real-time feedback
                            if scanned % 10 == 0 or scanned == 1 or scanned == batch_offset + 1:
                                self.update_progress(current_total, total_to_scan,
                                                   file_result.file_path, 'scanning')

                            # Commit based on scan size to balance real-time updates with performance
                            # For small scans (<20 files), update EVERY file for immediate UI feedback
                            # For medium scans, update every 5-10 files
                            # For large scans, update less frequently to reduce database overhead
                            if total_to_scan < 20:
                                update_threshold = 1  # Update every file for small scans
                            elif total_to_scan < 100:
                                update_threshold = 5  # Update every 5 files for medium scans
                            elif total_to_scan < 1000:
                                update_threshold = 10  # Update every 10 files
                            else:
                                update_threshold = 50  # Update every 50 files for large scans

                            if scan_state and (scanned - last_commit_count) >= update_threshold:
                                try:
                                    scan_state.files_processed = current_total
                                    scan_state.update_progress(current_total, total_to_scan, current_file=file_result.file_path)

                                    # Update progress message with current file info
                                    from utils import ProgressTracker
                                    progress_tracker = ProgressTracker('scan')
                                    scan_state.progress_message = progress_tracker.get_progress_message(
                                        f'Phase 3 of 3: Scanning files',
                                        current_total,
                                        total_to_scan,
                                        os.path.basename(file_result.file_path)
                                    )
                                    db.session.commit()
                                    last_commit_count = scanned

                                    # Memory management - cleanup every 1000 files
                                    if scanned % 1000 == 0:
                                        import gc
                                        gc.collect()
                                except Exception as e:
                                    logger.error(f"Failed to update progress for file {file_result.file_path}: {e}")
                                # Try to recover the database session
                                try:
                                    db.session.rollback()
                                    # Re-get scan state and try again without aggressive session cleanup
                                    scan_state = db.session.query(ScanState).filter_by(id=scan_state.id).first()
                                    if scan_state:
                                        scan_state.update_progress(current_total, total_to_scan, current_file=file_result.file_path)
                                        db.session.commit()
                                        last_commit_count = scanned
                                except Exception as e2:
                                    logger.error(f"Failed to recover progress update: {e2}")

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
                # Clear current_file when chunk is complete (don't show directory path as a file)
                scan_state.update_progress(final_total, total_to_scan, current_file='')
            
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
            
            # Update scan state with error recovery
            try:
                scan_state.current_chunk_index = i + 1
                scan_state.update_progress(files_scanned, len(selected_files), current_file='')
                scan_state.progress_message = progress_tracker.get_progress_message(
                    f'Scanning {len(selected_files)} selected files',
                    files_scanned,
                    len(selected_files),
                    os.path.basename(chunk.directory_path) if chunk_scanned > 0 else "Processing..."
                )
                db.session.commit()
            except Exception as e:
                logger.error(f"Failed to update progress for chunk {chunk.directory_path}: {e}")
                # Try to recover the database session
                try:
                    db.session.rollback()
                    # Re-get scan state and try again
                    scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                    if scan_state:
                        scan_state.update_progress(files_scanned, len(selected_files), current_file='')
                        db.session.commit()
                except Exception as e2:
                    logger.error(f"Failed to recover progress update: {e2}")
        
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
                scan_type = 'rescan'
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
                
                # Update scan state with error recovery
                try:
                    scan_state.current_chunk_index = completed_chunks
                    scan_state.update_progress(files_scanned, len(selected_files), current_file='')
                    scan_state.progress_message = progress_tracker.get_progress_message(
                        f'Scanning {len(selected_files)} selected files (parallel)',
                        files_scanned,
                        len(selected_files),
                        f"Completed {completed_chunks}/{len(chunks)} directories"
                    )
                    db.session.commit()
                except Exception as e:
                    logger.error(f"Failed to update progress for chunk {chunk.directory_path}: {e}")
                    # Try to recover the database session
                    try:
                        db.session.rollback()
                        # Re-get scan state and try again
                        scan_state = db.session.query(ScanState).filter_by(id=scan_state_id).first()
                        if scan_state:
                            scan_state.update_progress(files_scanned, len(selected_files), current_file='')
                            db.session.commit()
                    except Exception as e2:
                        logger.error(f"Failed to recover progress update: {e2}")
        
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
                scan_type = 'rescan'
                self._create_scan_report(completed_scan_state, scan_type=scan_type)
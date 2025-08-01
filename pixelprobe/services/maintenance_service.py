"""
Maintenance service for cleanup and file monitoring operations
"""

import os
import threading
import time
import logging
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional
import uuid

from media_checker import PixelProbe, load_exclusions
from models import db, ScanResult, CleanupState, FileChangesState, ScanReport
from utils import ProgressTracker

logger = logging.getLogger(__name__)

class MaintenanceService:
    """Service for maintenance operations like cleanup and file monitoring"""
    
    def __init__(self, database_uri: str):
        self.database_uri = database_uri
        self.cleanup_thread: Optional[threading.Thread] = None
        self.file_changes_thread: Optional[threading.Thread] = None
        
        # Cleanup state
        self.cleanup_state = {
            'is_running': False,
            'phase': 'idle',
            'files_processed': 0,
            'total_files': 0,
            'orphaned_found': 0,
            'progress_percentage': 0,
            'start_time': None,
            'cancel_requested': False
        }
        self.cleanup_lock = threading.Lock()
        
        # File changes state
        self.file_changes_state = {
            'is_running': False,
            'phase': 'idle',
            'files_processed': 0,
            'total_files': 0,
            'changes_found': 0,
            'corrupted_found': 0,
            'progress_percentage': 0,
            'start_time': None,
            'cancel_requested': False
        }
        self.file_changes_lock = threading.Lock()
    
    def start_cleanup(self) -> Dict:
        """Start cleanup of orphaned database entries"""
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            raise RuntimeError("Cleanup operation already in progress")
        
        # Reset state
        with self.cleanup_lock:
            self.cleanup_state.update({
                'is_running': True,
                'phase': 'starting',
                'files_processed': 0,
                'total_files': 0,
                'orphaned_found': 0,
                'progress_percentage': 0,
                'start_time': time.time(),
                'cancel_requested': False
            })
        
        # Create cleanup state in database
        cleanup_record = CleanupState(
            start_time=datetime.now(timezone.utc),
            is_active=True,
            phase='starting',
            phase_number=1
        )
        db.session.add(cleanup_record)
        db.session.commit()
        
        # Start cleanup in background
        self.cleanup_thread = threading.Thread(
            target=self._run_cleanup,
            args=(cleanup_record.id,)
        )
        self.cleanup_thread.start()
        
        return {
            'message': 'Cleanup operation started',
            'cleanup_id': cleanup_record.id
        }
    
    def start_file_changes_check(self) -> Dict:
        """Start checking for file changes"""
        if self.file_changes_thread and self.file_changes_thread.is_alive():
            raise RuntimeError("File changes check already in progress")
        
        # Create unique check ID
        check_id = str(uuid.uuid4())
        
        # Reset state
        with self.file_changes_lock:
            self.file_changes_state.update({
                'is_running': True,
                'phase': 'starting',
                'files_processed': 0,
                'total_files': 0,
                'changes_found': 0,
                'corrupted_found': 0,
                'progress_percentage': 0,
                'start_time': time.time(),
                'cancel_requested': False
            })
        
        # Create file changes state in database
        file_changes_record = FileChangesState(
            check_id=check_id,
            start_time=datetime.now(timezone.utc),
            is_active=True,
            phase='starting',
            phase_number=1
        )
        db.session.add(file_changes_record)
        db.session.commit()
        
        # Start file changes check in background
        self.file_changes_thread = threading.Thread(
            target=self._run_file_changes_check,
            args=(check_id,)
        )
        self.file_changes_thread.start()
        
        return {
            'message': 'File changes check started',
            'check_id': check_id
        }
    
    def get_cleanup_status(self) -> Dict:
        """Get current cleanup status"""
        with self.cleanup_lock:
            return self.cleanup_state.copy()
    
    def get_file_changes_status(self) -> Dict:
        """Get current file changes check status"""
        with self.file_changes_lock:
            return self.file_changes_state.copy()
    
    def cancel_cleanup(self) -> Dict:
        """Cancel the current cleanup operation"""
        cleanup_record = CleanupState.query.order_by(CleanupState.id.desc()).first()
        
        if cleanup_record and cleanup_record.is_active:
            if hasattr(cleanup_record, 'cancel_requested'):
                cleanup_record.cancel_requested = True
            cleanup_record.progress_message = 'Cancellation requested...'
            db.session.commit()
            
            with self.cleanup_lock:
                self.cleanup_state['cancel_requested'] = True
            
            return {'message': 'Cleanup cancellation requested'}
        else:
            raise RuntimeError("No active cleanup operation to cancel")
    
    def cancel_file_changes(self) -> Dict:
        """Cancel the current file changes check"""
        file_changes_record = FileChangesState.query.order_by(FileChangesState.id.desc()).first()
        
        if file_changes_record and file_changes_record.is_active:
            if hasattr(file_changes_record, 'cancel_requested'):
                file_changes_record.cancel_requested = True
            file_changes_record.progress_message = 'Cancellation requested...'
            db.session.commit()
            
            with self.file_changes_lock:
                self.file_changes_state['cancel_requested'] = True
            
            return {'message': 'File changes check cancellation requested'}
        else:
            raise RuntimeError("No active file changes check to cancel")
    
    def reset_cleanup_state(self) -> Dict:
        """Force reset cleanup state"""
        # Mark all active cleanups as failed
        active_cleanups = CleanupState.query.filter_by(is_active=True).all()
        for cleanup in active_cleanups:
            cleanup.is_active = False
            cleanup.phase = 'failed'
            cleanup.end_time = datetime.now(timezone.utc)
            cleanup.progress_message = 'Force reset by user'
        
        db.session.commit()
        
        # Reset in-memory state
        with self.cleanup_lock:
            self.cleanup_state.update({
                'is_running': False,
                'phase': 'idle',
                'files_processed': 0,
                'total_files': 0,
                'orphaned_found': 0,
                'progress_percentage': 0,
                'start_time': None,
                'cancel_requested': False
            })
        
        return {'message': 'Cleanup state reset successfully'}
    
    def _run_cleanup(self, cleanup_id: int):
        """Run the cleanup operation"""
        try:
            cleanup_record = CleanupState.query.get(cleanup_id)
            if not cleanup_record:
                logger.error(f"Cleanup record not found: {cleanup_id}")
                return
            
            # Phase 1: Scanning database
            cleanup_record.phase = 'scanning_database'
            cleanup_record.phase_number = 1
            cleanup_record.progress_message = 'Phase 1 of 3: Scanning database entries...'
            db.session.commit()
            
            # Get all database entries
            all_results = ScanResult.query.all()
            total_files = len(all_results)
            
            cleanup_record.total_files = total_files
            cleanup_record.phase_total = total_files
            db.session.commit()
            
            with self.cleanup_lock:
                self.cleanup_state['total_files'] = total_files
                self.cleanup_state['phase'] = 'scanning_database'
            
            # Phase 2: Checking and collecting orphaned files
            cleanup_record.phase = 'checking_files'
            cleanup_record.phase_number = 2
            cleanup_record.progress_message = f'Phase 2 of 3: Checking {total_files} files on filesystem...'
            db.session.commit()
            
            # Create progress tracker for cleanup
            progress_tracker = ProgressTracker('cleanup')
            
            orphaned_entries = []
            orphaned_count = 0
            
            for i, result in enumerate(all_results):
                if self._is_cancelled(cleanup_record):
                    break
                
                # Update progress
                cleanup_record.files_processed = i + 1
                cleanup_record.phase_current = i + 1
                cleanup_record.current_file = result.file_path
                
                # Update progress message with current file and ETA
                cleanup_record.progress_message = progress_tracker.get_progress_message(
                    f'Phase 2 of 3: Checking {total_files} files on filesystem',
                    i + 1,
                    total_files,
                    os.path.basename(result.file_path)
                )
                
                # Check if file exists
                if not os.path.exists(result.file_path):
                    orphaned_entries.append(result)
                    orphaned_count += 1
                    cleanup_record.orphaned_found = orphaned_count
                    logger.info(f"Found orphaned entry: {result.file_path}")
                
                # Update progress periodically
                if i % 100 == 0:
                    cleanup_record.files_processed = i + 1
                    db.session.commit()
                    
                with self.cleanup_lock:
                    self.cleanup_state['files_processed'] = i + 1
                    self.cleanup_state['orphaned_found'] = orphaned_count
            
            # Check if cancelled before proceeding to deletion phase
            if self._is_cancelled(cleanup_record):
                logger.info("Cleanup cancelled before deletion phase")
                cleanup_record.phase = 'cancelled'
                cleanup_record.progress_message = 'Cleanup cancelled by user'
                cleanup_record.is_active = False
                cleanup_record.end_time = datetime.now(timezone.utc)
                db.session.commit()
                
                with self.cleanup_lock:
                    self.cleanup_state['is_running'] = False
                    self.cleanup_state['phase'] = 'cancelled'
                return
            
            # Phase 3: Delete orphaned entries from database
            if orphaned_entries:
                cleanup_record.phase = 'deleting_entries'
                cleanup_record.phase_number = 3
                cleanup_record.progress_message = f'Phase 3 of 3: Removing {orphaned_count} orphaned entries from database...'
                cleanup_record.total_files = len(orphaned_entries)
                cleanup_record.phase_total = len(orphaned_entries)
                cleanup_record.files_processed = 0
                cleanup_record.phase_current = 0
                db.session.commit()
                
                # Delete orphaned entries in batches for performance
                deleted_count = 0
                batch_size = 50
                
                for i in range(0, len(orphaned_entries), batch_size):
                    if self._is_cancelled(cleanup_record):
                        break
                        
                    batch = orphaned_entries[i:i + batch_size]
                    
                    for entry in batch:
                        db.session.delete(entry)
                        deleted_count += 1
                        logger.info(f"Deleted orphaned entry: {entry.file_path}")
                    
                    # Commit batch
                    db.session.commit()
                    
                    # Update progress
                    cleanup_record.files_processed = deleted_count
                    cleanup_record.phase_current = deleted_count
                    cleanup_record.current_file = f"Deleted {deleted_count}/{orphaned_count} entries"
                    db.session.commit()
                    
                    with self.cleanup_lock:
                        self.cleanup_state['files_processed'] = deleted_count
                
                logger.info(f"Successfully deleted {deleted_count} orphaned database entries")
            
            # Final commit
            db.session.commit()
            
            # Complete cleanup
            if self._is_cancelled(cleanup_record):
                cleanup_record.phase = 'cancelled'
                cleanup_record.progress_message = 'Cleanup cancelled by user'
            else:
                cleanup_record.phase = 'complete'
                if orphaned_count > 0:
                    deleted_count = len(orphaned_entries) if orphaned_entries else orphaned_count
                    cleanup_record.progress_message = f'Cleanup complete. Deleted {deleted_count} orphaned database entries.'
                else:
                    cleanup_record.progress_message = 'Cleanup complete. No orphaned entries found.'
            
            cleanup_record.is_active = False
            cleanup_record.end_time = datetime.now(timezone.utc)
            db.session.commit()
            
            # Create scan report for cleanup operation
            if cleanup_record.phase == 'complete':
                self._create_cleanup_report(cleanup_record)
            
            with self.cleanup_lock:
                self.cleanup_state['is_running'] = False
                self.cleanup_state['phase'] = cleanup_record.phase
                
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            self._handle_cleanup_error(cleanup_id, str(e))
    
    def _run_file_changes_check(self, check_id: str):
        """Run the file changes check operation"""
        try:
            file_changes_record = FileChangesState.query.filter_by(check_id=check_id).first()
            if not file_changes_record:
                logger.error(f"File changes record not found: {check_id}")
                return
            
            # Phase 1: Starting
            file_changes_record.phase = 'starting'
            file_changes_record.phase_number = 1
            file_changes_record.phase_total = 1
            file_changes_record.phase_current = 0
            file_changes_record.progress_message = 'Phase 1 of 3: Starting file changes check...'
            db.session.commit()
            
            # Get total count
            total_files = ScanResult.query.count()
            file_changes_record.total_files = total_files
            file_changes_record.phase_current = 1
            db.session.commit()
            
            # Phase 2: Checking hashes
            file_changes_record.phase = 'checking_hashes'
            file_changes_record.phase_number = 2
            file_changes_record.phase_total = total_files
            file_changes_record.phase_current = 0
            file_changes_record.files_processed = 0
            file_changes_record.progress_message = f'Phase 2 of 3: Checking {total_files} files for hash changes...'
            db.session.commit()
            
            logger.info(f"Starting file changes check for {total_files} files")
            
            # Create progress tracker for file changes
            progress_tracker = ProgressTracker('file_changes')
            
            excluded_paths, excluded_extensions = load_exclusions()
            checker = PixelProbe(
                database_path=self.database_uri,
                excluded_paths=excluded_paths,
                excluded_extensions=excluded_extensions
            )
            changed_files = []
            
            # Process files in smaller batches for better performance
            batch_size = 100  # Reduced from 1000 for better responsiveness
            last_id = 0
            files_processed = 0
            
            while files_processed < total_files:
                if self._is_cancelled_file_changes(file_changes_record):
                    break
                
                # Use ID-based pagination instead of offset for better performance
                try:
                    batch = ScanResult.query.filter(ScanResult.id > last_id).order_by(ScanResult.id).limit(batch_size).all()
                    
                    if not batch:
                        logger.info(f"No more files to process after ID {last_id}")
                        break
                        
                    if files_processed % 1000 == 0:
                        logger.info(f"Progress: {files_processed}/{total_files} files processed")
                except Exception as e:
                    logger.error(f"Error querying batch after ID {last_id}: {e}")
                    file_changes_record.progress_message = f"Error querying database: {str(e)}"
                    db.session.commit()
                    raise
                
                for result in batch:
                    if self._is_cancelled_file_changes(file_changes_record):
                        break
                    
                    files_processed += 1
                    last_id = result.id
                    
                    # Update progress in database immediately for each file
                    file_changes_record.files_processed = files_processed
                    file_changes_record.phase_current = files_processed
                    file_changes_record.current_file = result.file_path
                    
                    # Update progress message with current file and ETA
                    file_changes_record.progress_message = progress_tracker.get_progress_message(
                        f'Phase 2 of 3: Checking {total_files} files for hash changes',
                        files_processed,
                        total_files,
                        os.path.basename(result.file_path)
                    )
                    
                    # Check for changes
                    try:
                        change_info = self._check_file_changes(result, checker)
                        if change_info:
                            changed_files.append(change_info)
                            file_changes_record.changes_found = len(changed_files)
                    except Exception as e:
                        logger.error(f"Error checking file {result.file_path}: {e}")
                        # Continue processing other files even if one fails
                    
                    # Commit every few files to balance performance and reliability
                    if files_processed % 5 == 0:
                        try:
                            db.session.commit()
                        except Exception as e:
                            logger.error(f"Error committing progress at file {files_processed}: {e}")
                            # Try to refresh the session and continue
                            db.session.rollback()
                            file_changes_record = db.session.merge(file_changes_record)
                    
                    # Update in-memory state
                    with self.file_changes_lock:
                        self.file_changes_state['files_processed'] = files_processed
                        self.file_changes_state['changes_found'] = len(changed_files)
                
                # Ensure we commit at the end of each batch
                try:
                    db.session.commit()
                except Exception as e:
                    logger.error(f"Error committing batch progress: {e}")
                    db.session.rollback()
            
            # Phase 3: Rescanning changed files
            if changed_files and not self._is_cancelled_file_changes(file_changes_record):
                file_changes_record.phase = 'rescanning'
                file_changes_record.phase_number = 3
                file_changes_record.phase_total = len(changed_files)
                file_changes_record.phase_current = 0
                file_changes_record.progress_message = f'Phase 3 of 3: Rescanning {len(changed_files)} changed files...'
                db.session.commit()
                
                corrupted_found = 0
                for i, change_info in enumerate(changed_files):
                    if self._is_cancelled_file_changes(file_changes_record):
                        break
                    
                    file_changes_record.phase_current = i + 1
                    
                    # Rescan the file
                    try:
                        result = checker.scan_file(change_info['file_path'], force_rescan=True)
                        if result and result.is_corrupted:
                            corrupted_found += 1
                            file_changes_record.corrupted_found = corrupted_found
                    except Exception as e:
                        logger.error(f"Error rescanning {change_info['file_path']}: {e}")
                    
                    if (i + 1) % 10 == 0:
                        db.session.commit()
            
            # Complete check
            if self._is_cancelled_file_changes(file_changes_record):
                file_changes_record.phase = 'cancelled'
                file_changes_record.progress_message = 'File changes check cancelled by user'
            else:
                file_changes_record.phase = 'complete'
                file_changes_record.progress_message = (
                    f'Check complete. Found {len(changed_files)} changed files, '
                    f'{file_changes_record.corrupted_found} newly corrupted.'
                )
            
            file_changes_record.is_active = False
            file_changes_record.end_time = datetime.now(timezone.utc)
            db.session.commit()
            
            # Create scan report for file changes operation
            if file_changes_record.phase == 'complete':
                self._create_file_changes_report(file_changes_record)
            
            with self.file_changes_lock:
                self.file_changes_state['is_running'] = False
                self.file_changes_state['phase'] = file_changes_record.phase
                
        except Exception as e:
            logger.error(f"Error during file changes check: {e}")
            self._handle_file_changes_error(check_id, str(e))
    
    def _check_file_changes(self, result: ScanResult, checker: PixelProbe) -> Optional[Dict]:
        """Check if a file has changed since last scan"""
        if not os.path.exists(result.file_path):
            return {
                'file_path': result.file_path,
                'change_type': 'deleted',
                'stored_hash': result.file_hash,
                'current_hash': None
            }
        
        try:
            # Get current file stats
            stat = os.stat(result.file_path)
            current_modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            
            # Check modification time
            # Ensure timezone-aware comparison
            stored_modified = result.last_modified
            if stored_modified and stored_modified.tzinfo is None:
                # If stored datetime is naive, assume UTC
                stored_modified = stored_modified.replace(tzinfo=timezone.utc)
            
            if stored_modified and current_modified > stored_modified:
                # File has been modified, calculate new hash
                current_hash = self._calculate_file_hash(result.file_path)
                
                if current_hash != result.file_hash:
                    return {
                        'file_path': result.file_path,
                        'change_type': 'modified',
                        'stored_hash': result.file_hash,
                        'current_hash': current_hash,
                        'stored_modified': stored_modified,
                        'current_modified': current_modified
                    }
        except Exception as e:
            logger.error(f"Error checking file {result.file_path}: {e}")
        
        return None
    
    def _calculate_file_hash(self, file_path: str) -> str:
        """Calculate SHA256 hash of a file"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    def _is_cancelled(self, cleanup_record: CleanupState) -> bool:
        """Check if cleanup has been cancelled"""
        try:
            # Force a fresh read from database
            db.session.expire(cleanup_record)
            db.session.refresh(cleanup_record)
            is_cancelled = getattr(cleanup_record, 'cancel_requested', False) or self.cleanup_state.get('cancel_requested', False)
            if is_cancelled:
                logger.info(f"Cleanup cancellation detected - DB: {cleanup_record.cancel_requested}, Memory: {self.cleanup_state.get('cancel_requested', False)}")
            return is_cancelled
        except Exception as e:
            logger.warning(f"Error checking cancel status from DB: {e}")
            return self.cleanup_state.get('cancel_requested', False)
    
    def _is_cancelled_file_changes(self, record: FileChangesState) -> bool:
        """Check if file changes check has been cancelled"""
        try:
            # Force a fresh read from database
            db.session.expire(record)
            db.session.refresh(record)
            is_cancelled = getattr(record, 'cancel_requested', False) or self.file_changes_state.get('cancel_requested', False)
            if is_cancelled:
                logger.info(f"File changes cancellation detected - DB: {record.cancel_requested}, Memory: {self.file_changes_state.get('cancel_requested', False)}")
            return is_cancelled
        except Exception as e:
            logger.warning(f"Error checking cancel status from DB: {e}")
            return self.file_changes_state.get('cancel_requested', False)
    
    def _handle_cleanup_error(self, cleanup_id: int, error_msg: str):
        """Handle cleanup error"""
        try:
            cleanup_record = CleanupState.query.get(cleanup_id)
            if cleanup_record:
                cleanup_record.phase = 'error'
                cleanup_record.is_active = False
                cleanup_record.end_time = datetime.now(timezone.utc)
                cleanup_record.progress_message = f'Error: {error_msg}'
                db.session.commit()
        except:
            pass
        
        with self.cleanup_lock:
            self.cleanup_state['is_running'] = False
            self.cleanup_state['phase'] = 'error'
    
    def _handle_file_changes_error(self, check_id: str, error_msg: str):
        """Handle file changes check error"""
        try:
            record = FileChangesState.query.filter_by(check_id=check_id).first()
            if record:
                record.phase = 'error'
                record.is_active = False
                record.end_time = datetime.now(timezone.utc)
                record.progress_message = f'Error: {error_msg}'
                db.session.commit()
        except:
            pass
        
        with self.file_changes_lock:
            self.file_changes_state['is_running'] = False
            self.file_changes_state['phase'] = 'error'
    
    def _create_cleanup_report(self, cleanup_record: CleanupState):
        """Create a scan report for cleanup operation"""
        try:
            # Calculate duration
            duration = None
            if cleanup_record.start_time and cleanup_record.end_time:
                duration = (cleanup_record.end_time - cleanup_record.start_time).total_seconds()
            
            # Create scan report
            report = ScanReport(
                scan_type='cleanup',
                start_time=cleanup_record.start_time,
                end_time=cleanup_record.end_time,
                duration_seconds=duration,
                total_files_discovered=cleanup_record.total_files,
                files_scanned=cleanup_record.files_processed,
                orphaned_records_found=cleanup_record.orphaned_found,
                orphaned_records_deleted=cleanup_record.orphaned_found,  # Assuming all found were deleted
                status='completed' if cleanup_record.phase == 'complete' else cleanup_record.phase,
                error_message=cleanup_record.error_message
            )
            
            db.session.add(report)
            db.session.commit()
            
            logger.info(f"Created cleanup report {report.report_id}")
            
        except Exception as e:
            logger.error(f"Failed to create cleanup report: {e}")
    
    def _create_file_changes_report(self, file_changes_record: FileChangesState):
        """Create a scan report for file changes operation"""
        try:
            # Calculate duration
            duration = None
            if file_changes_record.start_time and file_changes_record.end_time:
                duration = (file_changes_record.end_time - file_changes_record.start_time).total_seconds()
            
            # Create scan report
            report = ScanReport(
                scan_type='file_changes',
                start_time=file_changes_record.start_time,
                end_time=file_changes_record.end_time,
                duration_seconds=duration,
                total_files_discovered=file_changes_record.total_files,
                files_scanned=file_changes_record.files_processed,
                files_changed=file_changes_record.changes_found,
                files_corrupted_new=file_changes_record.corrupted_found,
                status='completed' if file_changes_record.phase == 'complete' else file_changes_record.phase,
                error_message=file_changes_record.error_message
            )
            
            db.session.add(report)
            db.session.commit()
            
            logger.info(f"Created file changes report {report.report_id}")
            
        except Exception as e:
            logger.error(f"Failed to create file changes report: {e}")
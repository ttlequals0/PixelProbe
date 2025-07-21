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
from models import db, ScanResult, CleanupState, FileChangesState
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
            phase_number=1,
            total_phases=2
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
            phase_number=1,
            total_phases=3
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
            cleanup_record.progress_message = 'Phase 1 of 2: Scanning database entries...'
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
            
            # Phase 2: Checking filesystem
            cleanup_record.phase = 'checking_files'
            cleanup_record.phase_number = 2
            cleanup_record.progress_message = f'Phase 2 of 2: Checking {total_files} files on filesystem...'
            db.session.commit()
            
            orphaned_count = 0
            for i, result in enumerate(all_results):
                if self._is_cancelled(cleanup_record):
                    break
                
                # Update progress
                cleanup_record.files_processed = i + 1
                cleanup_record.phase_current = i + 1
                cleanup_record.current_file = result.file_path
                
                # Check if file exists
                if not os.path.exists(result.file_path):
                    result.file_exists = False
                    orphaned_count += 1
                    cleanup_record.orphaned_found = orphaned_count
                    logger.info(f"Found orphaned entry: {result.file_path}")
                else:
                    result.file_exists = True
                
                # Commit periodically
                if i % 100 == 0:
                    db.session.commit()
                    
                with self.cleanup_lock:
                    self.cleanup_state['files_processed'] = i + 1
                    self.cleanup_state['orphaned_found'] = orphaned_count
            
            # Final commit
            db.session.commit()
            
            # Complete cleanup
            if self._is_cancelled(cleanup_record):
                cleanup_record.phase = 'cancelled'
                cleanup_record.progress_message = 'Cleanup cancelled by user'
            else:
                cleanup_record.phase = 'complete'
                cleanup_record.progress_message = f'Cleanup complete. Found {orphaned_count} orphaned entries.'
            
            cleanup_record.is_active = False
            cleanup_record.end_time = datetime.now(timezone.utc)
            db.session.commit()
            
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
            file_changes_record.progress_message = 'Phase 1 of 3: Starting file changes check...'
            db.session.commit()
            
            # Get total count
            total_files = ScanResult.query.count()
            file_changes_record.total_files = total_files
            db.session.commit()
            
            # Phase 2: Checking hashes
            file_changes_record.phase = 'checking_hashes'
            file_changes_record.phase_number = 2
            file_changes_record.phase_total = total_files
            file_changes_record.progress_message = f'Phase 2 of 3: Checking {total_files} files for hash changes...'
            db.session.commit()
            
            excluded_paths, excluded_extensions = load_exclusions()
            checker = PixelProbe(
                database_path=self.database_uri,
                excluded_paths=excluded_paths,
                excluded_extensions=excluded_extensions
            )
            changed_files = []
            
            # Process files in batches
            batch_size = 1000
            for offset in range(0, total_files, batch_size):
                if self._is_cancelled_file_changes(file_changes_record):
                    break
                
                batch = ScanResult.query.offset(offset).limit(batch_size).all()
                
                for result in batch:
                    if self._is_cancelled_file_changes(file_changes_record):
                        break
                    
                    file_changes_record.files_processed += 1
                    file_changes_record.phase_current = file_changes_record.files_processed
                    file_changes_record.current_file = result.file_path
                    
                    # Check for changes
                    change_info = self._check_file_changes(result, checker)
                    if change_info:
                        changed_files.append(change_info)
                        file_changes_record.changes_found = len(changed_files)
                    
                    # Update periodically
                    if file_changes_record.files_processed % 100 == 0:
                        db.session.commit()
            
            # Phase 3: Rescanning changed files
            if changed_files and not self._is_cancelled_file_changes(file_changes_record):
                file_changes_record.phase = 'rescanning'
                file_changes_record.phase_number = 3
                file_changes_record.phase_total = len(changed_files)
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
            if result.last_modified and current_modified > result.last_modified:
                # File has been modified, calculate new hash
                current_hash = self._calculate_file_hash(result.file_path)
                
                if current_hash != result.file_hash:
                    return {
                        'file_path': result.file_path,
                        'change_type': 'modified',
                        'stored_hash': result.file_hash,
                        'current_hash': current_hash,
                        'stored_modified': result.last_modified,
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
        db.session.refresh(cleanup_record)
        return cleanup_record.cancel_requested or self.cleanup_state.get('cancel_requested', False)
    
    def _is_cancelled_file_changes(self, record: FileChangesState) -> bool:
        """Check if file changes check has been cancelled"""
        db.session.refresh(record)
        return record.cancel_requested or self.file_changes_state.get('cancel_requested', False)
    
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
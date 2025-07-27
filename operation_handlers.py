"""Unified operation handlers to reduce code redundancy"""

import logging
import time
from datetime import datetime, timezone
from utils import ProgressTracker, log_operation_status, batch_process, mark_operation_complete, mark_operation_error

logger = logging.getLogger(__name__)


class BaseOperationHandler:
    """Base class for all operation handlers (scan, cleanup, file-changes)
    
    This provides common functionality and reduces code duplication across operations.
    """
    
    def __init__(self, operation_type, db, app):
        self.operation_type = operation_type
        self.db = db
        self.app = app
        self.progress_tracker = ProgressTracker(operation_type)
        self.start_time = None
        self.is_cancelled = False
        
    def check_cancellation(self, state_record):
        """Check if operation has been cancelled"""
        if state_record and not state_record.is_active:
            self.is_cancelled = True
            log_operation_status(self.operation_type, 'cancel')
            return True
        return False
    
    def update_progress(self, state_record, **kwargs):
        """Update progress in database"""
        if state_record:
            for key, value in kwargs.items():
                if hasattr(state_record, key):
                    setattr(state_record, key, value)
            self.db.session.commit()
    
    def handle_error(self, state_record, error_message):
        """Handle operation errors consistently"""
        logger.error(f"Error in {self.operation_type}: {error_message}")
        if state_record:
            mark_operation_error(state_record, error_message)
            self.db.session.commit()
    
    def complete_operation(self, state_record, message=None):
        """Mark operation as complete"""
        if state_record:
            mark_operation_complete(state_record, message)
            self.db.session.commit()
    
    def get_progress_message(self, phase_name, files_processed=0, total_files=0, current_file=None):
        """Get formatted progress message"""
        return self.progress_tracker.get_progress_message(
            phase_name, files_processed, total_files, current_file
        )
    
    def log_batch_progress(self, batch_num, total_batches, batch_start_time):
        """Log batch processing progress"""
        batch_time = time.time() - batch_start_time
        logger.info(f"Completed batch {batch_num}/{total_batches} in {batch_time:.1f}s")


def create_async_operation_handler(operation_type):
    """Factory function to create operation handlers with common error handling
    
    This eliminates the redundant async operation patterns.
    """
    def wrapper(operation_func):
        def async_handler(app, db, operation_id, *args, **kwargs):
            with app.app_context():
                try:
                    # Run the actual operation
                    result = operation_func(app, db, operation_id, *args, **kwargs)
                    return result
                except Exception as e:
                    logger.error(f"Error in {operation_type} operation: {str(e)}")
                    # Update state to error
                    try:
                        if operation_type == 'cleanup':
                            from models import CleanupState
                            state = CleanupState.query.filter_by(cleanup_id=operation_id).first()
                        elif operation_type == 'file_changes':
                            from models import FileChangesState
                            state = FileChangesState.query.filter_by(check_id=operation_id).first()
                        else:
                            state = None
                            
                        if state:
                            mark_operation_error(state, str(e))
                            db.session.commit()
                    except:
                        logger.error(f"Failed to update {operation_type} state to error")
                    raise
        return async_handler
    return wrapper
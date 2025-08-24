"""
Patches for v2.2.47 to fix remaining database and connection issues
"""

import logging
from functools import wraps
from sqlalchemy.exc import OperationalError, InvalidRequestError, ResourceClosedError

logger = logging.getLogger(__name__)


def safe_count(query):
    """
    Safely execute a count query with proper error handling
    
    Fixes: NoSuchColumnError: Could not locate column in row for column 'count(*)'
    """
    try:
        # Standard count() method should work
        return query.count()
    except Exception as e:
        if "count(*)" in str(e) or "ResourceClosedError" in str(e):
            # Connection issue or result closed - try alternative approach
            try:
                # Use func.count with explicit column
                from sqlalchemy import func
                return query.session.query(func.count()).select_from(query.subquery()).scalar() or 0
            except Exception:
                # Last resort - return 0 to prevent crashes
                logger.error(f"Count query failed completely: {e}")
                return 0
        raise


def with_transaction_recovery(func):
    """
    Decorator to handle transaction state errors
    
    Fixes: InFailedSqlTransaction errors
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        from models import db
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except InvalidRequestError as e:
                if "transaction" in str(e).lower():
                    logger.warning(f"Transaction error on attempt {attempt + 1}: {e}")
                    db.session.rollback()
                    if attempt < max_retries - 1:
                        continue
                raise
            except OperationalError as e:
                if "lost synchronization" in str(e) or "closed the connection" in str(e):
                    logger.warning(f"Connection lost on attempt {attempt + 1}: {e}")
                    db.session.rollback()
                    db.session.close()
                    if attempt < max_retries - 1:
                        # Force new connection
                        db.session.bind.dispose()
                        continue
                raise
            except ResourceClosedError as e:
                logger.warning(f"Resource closed error on attempt {attempt + 1}: {e}")
                db.session.rollback()
                if attempt < max_retries - 1:
                    continue
                raise
                
        return None
    
    return wrapper


def patch_scan_service():
    """
    Apply patches to scan_service module
    """
    try:
        from pixelprobe.services import scan_service
        
        # Patch the count query in scan_service
        original_run_scan = scan_service.ScanService.run_scan
        
        @with_transaction_recovery
        def patched_run_scan(self, *args, **kwargs):
            return original_run_scan(self, *args, **kwargs)
        
        scan_service.ScanService.run_scan = patched_run_scan
        logger.info("Patched scan_service.run_scan with transaction recovery")
        
    except Exception as e:
        logger.error(f"Failed to patch scan_service: {e}")


def patch_database_queries():
    """
    Patch all database query methods to handle connection issues
    """
    from models import db
    
    # Add disposal method to handle lost connections
    original_execute = db.session.execute
    
    def safe_execute(statement, *args, **kwargs):
        """Execute with connection recovery"""
        try:
            return original_execute(statement, *args, **kwargs)
        except OperationalError as e:
            if "lost synchronization" in str(e):
                logger.warning("Lost synchronization, resetting connection")
                db.session.rollback()
                db.session.close()
                # Try again with fresh connection
                return original_execute(statement, *args, **kwargs)
            raise
    
    db.session.execute = safe_execute
    logger.info("Patched database execute with connection recovery")


def apply_all_patches():
    """Apply all v2.2.47 patches"""
    patch_scan_service()
    patch_database_queries()
    logger.info("All v2.2.47 patches applied")
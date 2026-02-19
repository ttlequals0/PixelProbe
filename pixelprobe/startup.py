"""
Startup cleanup routines for PixelProbe.

These functions run during application initialization to clean up
state from previous runs (e.g., crashed scans, bloated records).
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def cleanup_stuck_operations(db):
    """Clean up any stuck operations from previous runs"""
    try:
        from models import FileChangesState, CleanupState

        active_file_changes = FileChangesState.query.filter_by(is_active=True).all()
        for file_change in active_file_changes:
            file_change.is_active = False
            file_change.phase = 'failed'
            file_change.end_time = datetime.now(timezone.utc)
            file_change.progress_message = 'Application restarted - operation marked as failed'
            logger.warning(f"Marking stuck file changes operation {file_change.check_id} as failed")

        active_cleanups = CleanupState.query.filter_by(is_active=True).all()
        for cleanup in active_cleanups:
            cleanup.is_active = False
            cleanup.phase = 'failed'
            cleanup.end_time = datetime.now(timezone.utc)
            cleanup.progress_message = 'Application restarted - operation marked as failed'
            logger.warning(f"Marking stuck cleanup operation {cleanup.cleanup_id} as failed")

        if active_file_changes or active_cleanups:
            db.session.commit()
            logger.info(f"Cleaned up {len(active_file_changes)} stuck file changes and {len(active_cleanups)} stuck cleanup operations")

    except Exception as e:
        logger.error(f"Error cleaning up stuck operations: {str(e)}")


def cleanup_stuck_scans(db):
    """Clean up ALL active scans from previous runs - they can't still be running after restart."""
    try:
        from models import ScanState
        stuck_scans = ScanState.query.filter(
            ScanState.is_active == True
        ).all()

        for scan in stuck_scans:
            logger.warning(f"Found active scan {scan.id} from {scan.start_time}, marking as crashed (app restarted)")
            scan.is_active = False
            scan.phase = 'crashed'
            scan.error_message = "Application restarted - scan was interrupted"

        if stuck_scans:
            db.session.commit()
            logger.info(f"Cleaned up {len(stuck_scans)} abandoned scans from previous run")
    except Exception as e:
        logger.warning(f"Could not clean up stuck scans on startup: {e}")


def cleanup_bloated_scan_results(db):
    """Clean up bloated scan results from pre-v2.4.213.

    Files with large scan_output or warning_details (>50KB) stored thousands
    of lines in the old format. Delete them so they get rescanned with the
    efficient storage format.
    """
    try:
        from models import ScanResult
        bloated_results = db.session.query(ScanResult).filter(
            db.or_(
                db.func.length(ScanResult.scan_output) > 50000,
                db.func.length(ScanResult.warning_details) > 50000
            )
        ).all()

        if bloated_results:
            logger.info(f"Found {len(bloated_results)} scan results with bloated output fields (pre-v2.4.213 format)")
            logger.info("Deleting bloated records to trigger efficient rescan with v2.4.213+ format")

            for result in bloated_results:
                db.session.delete(result)

            db.session.commit()
            logger.info(f"Deleted {len(bloated_results)} bloated scan results - they will be rescanned with efficient storage")
        else:
            logger.debug("No bloated scan results found - database is clean")
    except Exception as e:
        logger.warning(f"Could not clean up bloated scan results on startup: {e}")

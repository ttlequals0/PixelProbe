"""
Startup cleanup routines for PixelProbe.

These functions run during application initialization to clean up
state from previous runs (e.g., crashed scans, bloated records).
"""

import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def cleanup_stuck_operations(db):
    """Clean up any stuck operations from previous runs"""
    try:
        from pixelprobe.models import FileChangesState, CleanupState

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
    """Mark abandoned scans from a previous run as crashed.

    Only scans with NO recent progress are crashed. PixelProbe runs the web app
    and the Celery worker as SEPARATE containers, so an app-container restart
    must NOT crash a scan that is still actively running in the worker (it keeps
    writing last_update). A scan whose last activity is older than the grace
    window (default 30 min, matching the periodic stuck-scan check) is treated as
    genuinely dead and crashed; anything still progressing is left alone and the
    periodic checker handles it if it later goes stale.
    """
    try:
        from pixelprobe.models import ScanState
        try:
            grace_secs = max(60, int(os.environ.get('STUCK_SCAN_STARTUP_GRACE_SECS', '1800')))
        except (TypeError, ValueError):
            grace_secs = 1800
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=grace_secs)

        active_scans = ScanState.query.filter(ScanState.is_active == True).all()
        crashed = 0
        for scan in active_scans:
            last_activity = scan.last_update or scan.start_time
            if last_activity and last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=timezone.utc)
            if last_activity and last_activity > cutoff:
                logger.info(f"Active scan {scan.id} last updated {last_activity} (within grace); "
                            f"leaving it running across this restart")
                continue
            logger.warning(f"Marking abandoned scan {scan.id} (last activity {last_activity}) as crashed")
            scan.is_active = False
            scan.phase = 'crashed'
            scan.error_message = "Application restarted - scan was interrupted"
            crashed += 1

        if crashed:
            db.session.commit()
            logger.info(f"Cleaned up {crashed} abandoned scans from previous run")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not clean up stuck scans on startup: {e}")


def cleanup_bloated_scan_results(db):
    """Clean up bloated scan results from pre-v2.4.213.

    Files with large scan_output or warning_details (>50KB) stored thousands
    of lines in the old format. Delete them so they get rescanned with the
    efficient storage format.
    """
    try:
        from pixelprobe.models import ScanResult
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

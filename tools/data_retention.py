#!/usr/bin/env python3
"""
Data Retention Policy Implementation for PixelProbe

Automatically cleans up old data to prevent unbounded database growth.
Runs daily via Celery Beat scheduler.

Retention Policies:
- scan_output: DISABLED - keeps all scan_results data forever (including full scan_output)
- reports: Delete after 90 days
- scan_state: Delete completed/failed states after 7 days

Note: scan_output archival is intentionally disabled to preserve all scan result
details. The cleanup_scan_outputs() function remains in the code but is not called.

Usage:
    # Via Celery (automatic daily)
    Scheduled via celery beat

    # Manual execution
    python tools/data_retention.py

    # From Python
    from tools.data_retention import run_all_retention_policies
    results = run_all_retention_policies()
"""

import sys
import os
import logging
from datetime import datetime, timedelta, timezone

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from pixelprobe.models import db, ScanResult, ScanReport, ScanState

logger = logging.getLogger(__name__)

# Retention periods (configurable via environment variables)
SCAN_OUTPUT_RETENTION_DAYS = int(os.getenv('SCAN_OUTPUT_RETENTION_DAYS', '30'))
REPORT_RETENTION_DAYS = int(os.getenv('REPORT_RETENTION_DAYS', '90'))
SCAN_STATE_RETENTION_DAYS = int(os.getenv('SCAN_STATE_RETENTION_DAYS', '7'))


def cleanup_scan_outputs():
    """
    Archive scan_output for old successful scans.

    Keeps first 100 characters of scan_output, truncates the rest.
    Only affects successfully scanned files to preserve error details for investigation.

    Returns:
        int: Number of records updated
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=SCAN_OUTPUT_RETENTION_DAYS)

    logger.info(f"Archiving scan_output older than {cutoff_date.strftime('%Y-%m-%d')} ({SCAN_OUTPUT_RETENTION_DAYS} days)")

    try:
        # Keep first 100 chars, truncate rest for old successful scans
        result = db.session.execute(
            text("""
                UPDATE scan_results
                SET scan_output = CASE
                    WHEN LENGTH(scan_output) > 100
                    THEN SUBSTR(scan_output, 1, 100) || '...[archived]'
                    ELSE scan_output
                END
                WHERE scan_date < :cutoff
                AND scan_status = 'scanned'
                AND scan_output IS NOT NULL
                AND LENGTH(scan_output) > 100
                AND scan_output NOT LIKE '%[archived]%'
            """),
            {'cutoff': cutoff_date}
        )

        rows_updated = result.rowcount
        logger.info(f"Archived scan_output for {rows_updated} records")
        db.session.commit()

        return rows_updated

    except Exception as e:
        logger.error(f"Error archiving scan outputs: {e}")
        db.session.rollback()
        raise


def cleanup_old_reports():
    """
    Delete scan reports older than retention period.

    Reports are snapshots of scan results at a point in time.
    Old reports can be safely deleted as the underlying data still exists.

    Returns:
        int: Number of reports deleted
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=REPORT_RETENTION_DAYS)

    logger.info(f"Deleting reports older than {cutoff_date.strftime('%Y-%m-%d')} ({REPORT_RETENTION_DAYS} days)")

    try:
        deleted = ScanReport.query.filter(
            ScanReport.start_time < cutoff_date
        ).delete()

        db.session.commit()
        logger.info(f"Deleted {deleted} old reports")

        return deleted

    except Exception as e:
        logger.error(f"Error deleting old reports: {e}")
        db.session.rollback()
        raise


def cleanup_old_scan_states():
    """
    Delete old scan_state records for completed/failed scans.

    Scan state records track active scans and their progress.
    Once completed/failed/cancelled, they're only needed for recent history.
    Active scans are never deleted regardless of age.

    Returns:
        int: Number of scan states deleted
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=SCAN_STATE_RETENTION_DAYS)

    logger.info(f"Deleting scan states older than {cutoff_date.strftime('%Y-%m-%d')} ({SCAN_STATE_RETENTION_DAYS} days)")

    try:
        # Only delete completed/failed/cancelled scans, keep active ones
        deleted = ScanState.query.filter(
            ScanState.last_update < cutoff_date,
            ScanState.phase.in_(['completed', 'error', 'cancelled'])
        ).delete()

        db.session.commit()
        logger.info(f"Deleted {deleted} old scan state records")

        return deleted

    except Exception as e:
        logger.error(f"Error deleting old scan states: {e}")
        db.session.rollback()
        raise


def get_retention_stats():
    """
    Get statistics about data that would be affected by retention policies.
    Useful for understanding impact before running cleanup.

    Returns:
        dict: Statistics about archivable data
    """
    stats = {
        'scan_outputs': {},
        'reports': {},
        'scan_states': {}
    }

    try:
        # Scan output stats
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=SCAN_OUTPUT_RETENTION_DAYS)
        result = db.session.execute(
            text("""
                SELECT
                    COUNT(*) as total,
                    SUM(LENGTH(scan_output)) as total_bytes
                FROM scan_results
                WHERE scan_date < :cutoff
                AND scan_status = 'scanned'
                AND scan_output IS NOT NULL
                AND LENGTH(scan_output) > 100
                AND scan_output NOT LIKE '%[archived]%'
            """),
            {'cutoff': cutoff_date}
        )
        row = result.fetchone()
        stats['scan_outputs'] = {
            'total_records': row[0] if row else 0,
            'total_bytes': row[1] if row and row[1] else 0
        }

        # Report stats
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=REPORT_RETENTION_DAYS)
        stats['reports']['total'] = ScanReport.query.filter(
            ScanReport.start_time < cutoff_date
        ).count()

        # Scan state stats
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=SCAN_STATE_RETENTION_DAYS)
        stats['scan_states']['total'] = ScanState.query.filter(
            ScanState.last_update < cutoff_date,
            ScanState.phase.in_(['completed', 'error', 'cancelled'])
        ).count()

        return stats

    except Exception as e:
        logger.error(f"Error getting retention stats: {e}")
        return stats


def run_all_retention_policies(dry_run=False):
    """
    Execute all retention policies in sequence.

    NOTE: scan_output archival is DISABLED to preserve all scan result details.
    Only reports and scan states are cleaned up.

    Args:
        dry_run: report what would be deleted without deleting anything

    Returns:
        dict: Results from each retention policy
    """
    logger.info("=" * 60)
    logger.info("Starting data retention cleanup")
    logger.info(f"Retention periods:")
    logger.info(f"  - Scan outputs: DISABLED (keeping all scan_output data)")
    logger.info(f"  - Reports: {REPORT_RETENTION_DAYS} days")
    logger.info(f"  - Scan states: {SCAN_STATE_RETENTION_DAYS} days")
    logger.info("=" * 60)

    try:
        # Get stats before cleanup
        logger.info("Getting retention statistics...")
        stats_before = get_retention_stats()
        logger.info(f"Before cleanup:")
        logger.info(f"  - Reports to delete: {stats_before['reports']['total']:,}")
        logger.info(f"  - Scan states to delete: {stats_before['scan_states']['total']:,}")

        if dry_run:
            logger.info("DRY RUN - nothing deleted")
            return {
                'success': True,
                'dry_run': True,
                'outputs_archived': 0,
                'reports_deleted': 0,
                'states_deleted': 0,
                'stats_before': stats_before
            }

        # Run cleanup policies (scan_output archival DISABLED - keeps all scan results data)
        # outputs_archived = cleanup_scan_outputs()  # DISABLED - keeps full scan_output forever
        reports_deleted = cleanup_old_reports()
        states_deleted = cleanup_old_scan_states()

        logger.info("=" * 60)
        logger.info(f"Retention cleanup complete:")
        logger.info(f"  - {reports_deleted:,} reports deleted")
        logger.info(f"  - {states_deleted:,} scan states deleted")
        logger.info("=" * 60)

        return {
            'success': True,
            'outputs_archived': 0,  # Always 0 - scan_output archival disabled
            'reports_deleted': reports_deleted,
            'states_deleted': states_deleted,
            'stats_before': stats_before
        }

    except Exception as e:
        logger.error(f"Retention cleanup failed: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }


if __name__ == '__main__':
    # Can be run standalone for manual execution
    import argparse
    parser = argparse.ArgumentParser(description='Run data retention cleanup')
    parser.add_argument('--dry-run', action='store_true',
                        help='report what would be deleted without deleting anything')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Initialize Flask app context for database access
    from app import app
    with app.app_context():
        result = run_all_retention_policies(dry_run=args.dry_run)

        if result['success']:
            print("\nData retention cleanup " +
                  ("dry run finished (nothing deleted)" if args.dry_run else "completed successfully"))
            sys.exit(0)
        else:
            print(f"\nData retention cleanup failed: {result.get('error')}")
            sys.exit(1)

"""Celery-free core of the chunk-distributed scan engine.

Holds the scan-slot claim, chunk building, and scan finalization so the API
layer, the scheduler sweeper, and unit tests can use them without importing
Celery task modules (whose import chain requires the full app).
"""
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import text, update

from pixelprobe.constants import SCAN_PHASES, TERMINAL_SCAN_PHASES
from pixelprobe.models import db, ScanState, ScanResult, ScanChunk
from pixelprobe.progress_utils import clear_scan_progress_redis
from pixelprobe.services.scan_reporting import create_scan_report

logger = logging.getLogger(__name__)


def release_scan_claim(scan_id):
    """Release a claimed scan slot after a failed launch (single UPDATE, no
    ORM read, so a transient query error cannot leave the slot stuck)."""
    try:
        db.session.execute(
            update(ScanState)
            .where(ScanState.scan_id == scan_id, ScanState.is_active == True)
            .values(is_active=False, phase=SCAN_PHASES['ERROR'],
                    error_message='Scan launch failed',
                    end_time=datetime.now(timezone.utc))
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


def claim_scan_slot(scan_id, scan_type='full'):
    """Atomically claim the single scan slot via row lock.

    Returns (ok, error_payload, error_status); error fields are None on success.
    """
    try:
        scan_state = db.session.query(ScanState).with_for_update(nowait=True).first()
        if not scan_state:
            scan_state = ScanState()
            db.session.add(scan_state)
            db.session.flush()
            scan_state = db.session.query(ScanState).with_for_update(nowait=True).first()

        if scan_state.is_active and scan_state.phase not in TERMINAL_SCAN_PHASES:
            phase_info = f" (Phase: {scan_state.phase}, Files processed: {scan_state.files_processed})"
            db.session.rollback()
            return False, {
                'error': f'A scan is already in progress{phase_info}. Please wait for it to '
                         f'complete or use /api/cancel-scan to stop it.'
            }, 409

        # The table can hold extra rows (single-file rescans create their own);
        # an active one anywhere blocks a new directory scan.
        other_active = ScanState.query.filter(
            ScanState.is_active == True,
            ScanState.id != scan_state.id,
            ScanState.phase.notin_(TERMINAL_SCAN_PHASES)
        ).first()
        if other_active:
            db.session.rollback()
            return False, {
                'error': f'A scan is already in progress (Phase: {other_active.phase}). '
                         f'Please wait for it to complete or use /api/cancel-scan to stop it.'
            }, 409

        scan_state.scan_id = scan_id
        scan_state.is_active = True
        scan_state.phase = SCAN_PHASES['INITIALIZING']
        scan_state.scan_type = scan_type
        # Fresh claim markers so the stuck-scan sweeper does not judge this
        # claim by the previous scan's timestamps or dead Celery task
        scan_state.last_update = datetime.now(timezone.utc)
        scan_state.celery_task_id = None
        db.session.commit()
        return True, None, None

    except Exception as lock_error:
        db.session.rollback()
        logger.warning(f"Could not acquire scan lock: {lock_error}")
        return False, {
            'error': 'A scan is already starting. Please wait a moment and try again.'
        }, 409


def sync_progress_from_chunks(scan_state, scan_id):
    """Pull files_processed up to the chunk sum (never decreases - see the
    'x of 0' gotcha). Returns the chunk sum. Caller commits."""
    total = db.session.query(
        db.func.coalesce(db.func.sum(ScanChunk.files_scanned), 0)
    ).filter_by(scan_id=scan_id).scalar()
    if total > scan_state.files_processed:
        scan_state.files_processed = total
        scan_state.phase_current = total
    return total


def finalize_scan(scan_state):
    """Finalize a finished scan. Caller must hold the scan_state row lock.

    Errored chunks make the scan terminal as 'error' (not 'completed'), so the
    report and scheduled-scan healthcheck ping reflect the gap instead of
    claiming a clean run while thousands of files were returned to pending.
    """
    scan_id = scan_state.scan_id

    sync_progress_from_chunks(scan_state, scan_id)

    error_chunks = ScanChunk.query.filter_by(scan_id=scan_id, status='error').count()

    # Corrupted count scoped to this scan's window (ScanResult has no scan_id)
    corrupted = 0
    try:
        if scan_state.start_time:
            corrupted = ScanResult.query.filter(
                ScanResult.scan_date >= scan_state.start_time,
                ScanResult.is_corrupted == True
            ).count()
    except Exception as e:
        logger.error(f"Failed to count corrupted files for scan {scan_id}: {e}")

    # Rows left in 'scanning' by a dead chunk worker go back to pending
    reclaimed = ScanResult.reclaim_scanning()
    if reclaimed:
        logger.warning(f"Scan {scan_id}: reclaimed {reclaimed} files stuck in 'scanning'")

    if error_chunks:
        scan_state.phase = SCAN_PHASES['ERROR']
        scan_state.error_message = (
            f'{error_chunks} chunks failed; their unscanned files were returned to pending'
        )
        scan_state.progress_message = (
            f"Scan finished with errors: {scan_state.files_processed} files processed, "
            f"{corrupted} corrupted, {error_chunks} chunks failed"
        )
    else:
        scan_state.phase = SCAN_PHASES['COMPLETED']
        scan_state.progress_message = (
            f"Scan completed: {scan_state.files_processed} files processed, {corrupted} corrupted"
        )
    scan_state.is_active = False
    scan_state.end_time = datetime.now(timezone.utc)
    scan_state.last_update = datetime.now(timezone.utc)
    db.session.commit()
    logger.info(f"Scan {scan_id} finalized: {scan_state.files_processed} files, "
                f"{corrupted} corrupted, {error_chunks} failed chunks")

    create_scan_report(scan_state)
    try:
        clear_scan_progress_redis(scan_id)
    except Exception as e:
        logger.warning(f"Failed to clear Redis progress for scan {scan_id}: {e}")


def maybe_finalize_scan(scan_id: str):
    """Finalize the scan iff all of its chunks are terminal. Exactly-once via
    row lock. Called by every chunk task exit and by the stuck-scan sweeper.

    Applies only to chunk-engine scans: scan_type set by the claim/orchestrator
    AND at least one chunk. Legacy-engine scans (selected-file rescans) and
    scans that have not built chunks yet must never be finalized from here.
    """
    try:
        total = ScanChunk.query.filter_by(scan_id=scan_id).count()
        if total == 0:
            return False
        incomplete = ScanChunk.query.filter_by(scan_id=scan_id, is_complete=False).count()
        if incomplete:
            return False

        scan_state = ScanState.query.filter_by(scan_id=scan_id).with_for_update().first()
        if (not scan_state or not scan_state.is_active or not scan_state.scan_type
                or scan_state.phase != SCAN_PHASES['SCANNING']):
            db.session.commit()
            return False

        # Re-verify under the lock (another chunk may have won)
        incomplete = ScanChunk.query.filter_by(scan_id=scan_id, is_complete=False).count()
        if incomplete:
            db.session.commit()
            return False

        finalize_scan(scan_state)
        return True
    except Exception as e:
        logger.error(f"Finalization check failed for scan {scan_id}: {e}")
        db.session.rollback()
        return False


def build_scan_chunks(scan_id: str) -> List[Dict]:
    """Build disjoint FCP path-range chunks over all pending rows.

    A window query returns only the chunk boundary rows (first/last path per
    chunk), not all pending paths - at 1.2M files that is ~2,400 rows instead
    of 1.2M. Returns plain dicts ({'id', 'files_discovered'}) so the caller
    never touches expired ORM attributes after the commit.
    """
    total_pending = ScanResult.query.filter_by(scan_status='pending').count()
    if total_pending == 0:
        return []

    if total_pending <= 100:
        chunk_size = total_pending
    elif total_pending <= 1000:
        chunk_size = 100
    elif total_pending <= 10000:
        chunk_size = 500
    else:
        chunk_size = 1000

    # total_pending is passed as a bind (a count(*) OVER () window would
    # buffer the whole 1.2M-row partition before emitting the first row)
    rows = db.session.execute(text("""
        SELECT file_path, rn FROM (
            SELECT file_path,
                   row_number() OVER (ORDER BY file_path) AS rn
            FROM scan_results
            WHERE scan_status = 'pending'
        ) t
        WHERE rn % :size = 1 OR rn % :size = 0 OR rn = :total
        ORDER BY rn
    """), {'size': chunk_size, 'total': total_pending}).fetchall()

    chunks = []
    chunk_index = 0
    start_path = None
    start_rn = None

    for file_path, rn in rows:
        if start_path is None:
            start_path, start_rn = file_path, rn
        if rn % chunk_size == 0 or rn == total_pending:
            chunk_id = hashlib.md5(
                f"{scan_id}:scan_chunk_{chunk_index}:{time.time()}".encode()
            ).hexdigest()
            chunk = ScanChunk(
                scan_id=scan_id,
                chunk_id=chunk_id,
                directory_path=ScanChunk.fcp_directory_path(start_path, file_path),
                phase=SCAN_PHASES['SCANNING'],
                status='pending',
                files_discovered=rn - start_rn + 1,
                is_complete=False
            )
            db.session.add(chunk)
            chunks.append(chunk)
            chunk_index += 1
            start_path = start_rn = None

    db.session.flush()
    chunk_dicts = [{'id': c.id, 'files_discovered': c.files_discovered} for c in chunks]
    db.session.commit()
    logger.info(f"Created {len(chunk_dicts)} chunks for {total_pending} pending files (size {chunk_size})")
    return chunk_dicts

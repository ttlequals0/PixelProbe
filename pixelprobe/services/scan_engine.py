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

from sqlalchemy import select, text, update

from pixelprobe.constants import SCAN_PHASES, TERMINAL_SCAN_PHASES
from pixelprobe.models import db, ScanState, ScanResult, ScanChunk, ScanRunFile, ScanRunRoot, ScanNotificationOutbox, ScanReport
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
        # Serialize reservations while the requested run row is being created.
        # The unique scan_id constraint then provides the second line of
        # defense for concurrent API/Celery reservations.
        # Production requires PostgreSQL, where this is the cross-process
        # reservation. SQLite is retained only for unit tests and has no
        # distributed-concurrency guarantee.
        if db.session.get_bind().dialect.name == 'postgresql':
            db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                               {'key': 'pixelprobe:scan:global-slot'})
        # A run owns one immutable ScanState row. Never recycle the most
        # recent row for a new run: reports, membership, and cancellation all
        # depend on stable run identity.
        scan_state = (db.session.query(ScanState)
                      .filter(ScanState.scan_id == scan_id)
                      .with_for_update(nowait=True).first())
        if not scan_state:
            scan_state = ScanState()
            scan_state.scan_id = scan_id
            db.session.add(scan_state)
            db.session.flush()
            scan_state = (db.session.query(ScanState)
                          .filter(ScanState.scan_id == scan_id)
                          .with_for_update(nowait=True).first())

        if scan_state.phase != SCAN_PHASES['IDLE']:
            db.session.rollback()
            return False, {'error': 'Scan ID has already been reserved.'}, 409

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

        scan_state.is_active = True
        scan_state.phase = SCAN_PHASES['INITIALIZING']
        scan_state.scan_type = scan_type
        # Fresh claim markers so the stuck-scan sweeper does not judge this
        # claim by the previous scan's timestamps or dead Celery task
        scan_state.last_update = datetime.now(timezone.utc)
        scan_state.celery_task_id = None
        scan_state.dispatch_generation = (scan_state.dispatch_generation or 0) + 1
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

    if scan_state.phase == SCAN_PHASES['CANCELLED']:
        return False
    existing_report = ScanReport.query.filter_by(scan_id=scan_id).first()
    existing_outbox = ScanNotificationOutbox.query.filter_by(
        scan_id=scan_id, event='scan_completed').first()
    if existing_report and existing_outbox:
        return False

    sync_progress_from_chunks(scan_state, scan_id)

    error_chunks = ScanChunk.query.filter_by(scan_id=scan_id, status='error').count()

    # Rows left in 'scanning' by a dead chunk worker go back to pending, but
    # only when they belong to this run.
    stranded_ids = select(ScanRunFile.scan_result_id).where(
        ScanRunFile.scan_id == scan_id,
        ScanRunFile.status == 'processing',
        ScanRunFile.scan_result_id.isnot(None),
    )
    ScanResult.query.filter(ScanResult.id.in_(stranded_ids),
                            ScanResult.scan_status == 'scanning').update(
        {'scan_status': 'pending'}, synchronize_session=False)
    reclaimed = ScanRunFile.query.filter_by(scan_id=scan_id, status='processing').update(
        {'status': 'pending', 'claimed_at': None}, synchronize_session=False)
    if reclaimed:
        logger.warning(f"Scan {scan_id}: reclaimed {reclaimed} files stuck in 'scanning'")

    # Completion is a claim about this run's members and requested roots, not
    # a claim about whatever happens to be in ScanResult today. A terminal
    # chunk only says the worker returned; every member must have a successful
    # stable-content observation and every root must have been observed.
    member_total = ScanRunFile.query.filter_by(scan_id=scan_id).count()
    completed_members = ScanRunFile.query.filter_by(scan_id=scan_id, status='completed').count()
    pending_members = ScanRunFile.query.filter(
        ScanRunFile.scan_id == scan_id,
        ScanRunFile.status.in_(['pending', 'processing'])
    ).count()
    failed_members = ScanRunFile.query.filter(
        ScanRunFile.scan_id == scan_id,
        ~ScanRunFile.status.in_(['completed', 'pending', 'processing'])
    ).count()
    failed_roots = ScanRunRoot.query.filter(
        ScanRunRoot.scan_id == scan_id,
        ScanRunRoot.status != 'completed'
    ).count()
    expected_members = scan_state.estimated_total or 0
    coverage_gap = max(expected_members - member_total, 0)
    corrupted = ScanRunFile.query.filter_by(scan_id=scan_id, is_corrupted=True).count()
    failure_parts = []
    if scan_state.phase in (SCAN_PHASES['ERROR'], SCAN_PHASES['CRASHED'], 'interrupted'):
        failure_parts.append('scan execution ended before verification completed')
    if error_chunks:
        failure_parts.append(f'{error_chunks} chunks failed')
    if failed_roots:
        failure_parts.append(f'{failed_roots} roots were not completely observed')
    if pending_members:
        failure_parts.append(f'{pending_members} members remain pending')
    if failed_members:
        failure_parts.append(f'{failed_members} members were not successfully verified')
    if coverage_gap:
        failure_parts.append(f'{coverage_gap} expected members have no run membership')

    if failure_parts:
        scan_state.phase = SCAN_PHASES['ERROR']
        scan_state.error_message = '; '.join(failure_parts)
        scan_state.progress_message = (
            f"Scan finished with errors: {scan_state.files_processed} files processed, "
            f"{completed_members} successfully verified, {corrupted} corrupted"
        )
    else:
        scan_state.phase = SCAN_PHASES['COMPLETED']
        scan_state.progress_message = (
            f"Scan completed: {scan_state.files_processed} files processed, {corrupted} corrupted"
        )
    scan_state.is_active = False
    scan_state.end_time = datetime.now(timezone.utc)
    scan_state.last_update = datetime.now(timezone.utc)
    report = create_scan_report(scan_state, commit=False)
    outbox = ScanNotificationOutbox(scan_id=scan_id, event='scan_completed')
    db.session.add(outbox)
    db.session.flush()
    if not isinstance(report, ScanReport):
        raise RuntimeError(f'Could not create a report for scan {scan_id}')
    from pixelprobe.services.notification_service import snapshot_scan_notification_outbox
    snapshot_scan_notification_outbox(outbox, report)
    db.session.commit()
    try:
        from pixelprobe.tasks import deliver_scan_notification_outbox
        outbox = ScanNotificationOutbox.query.filter_by(scan_id=scan_id).first()
        deliver_scan_notification_outbox.apply_async(args=(outbox.id,))
    except Exception as exc:
        logger.warning(f"Scan {scan_id}: notification dispatch queued for recovery: {exc}")
    logger.info(f"Scan {scan_id} finalized: {scan_state.files_processed} files, "
                f"{corrupted} corrupted, {len(failure_parts)} completion gaps")

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


def build_scan_chunks(scan_id: str, commit: bool = True) -> List[Dict]:
    """Build disjoint FCP path-range chunks over all pending rows.

    A window query returns only the chunk boundary rows (first/last path per
    chunk), not all pending paths - at 1.2M files that is ~2,400 rows instead
    of 1.2M. Returns plain dicts ({'id', 'files_discovered'}) so the caller
    never touches expired ORM attributes after the commit.
    """
    total_pending = ScanRunFile.query.filter_by(scan_id=scan_id, status='pending').count()
    if not ScanState.query.filter_by(scan_id=scan_id).first():
        raise ValueError(f'Cannot build chunks for unreserved scan run {scan_id}')
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
            FROM scan_run_files
            WHERE scan_id = :scan_id AND status = 'pending'
        ) t
        WHERE rn % :size = 1 OR rn % :size = 0 OR rn = :total
        ORDER BY rn
    """), {'size': chunk_size, 'total': total_pending, 'scan_id': scan_id}).fetchall()

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
    if commit:
        db.session.commit()
    logger.info(f"Created {len(chunk_dicts)} chunks for {total_pending} pending files (size {chunk_size})")
    return chunk_dicts

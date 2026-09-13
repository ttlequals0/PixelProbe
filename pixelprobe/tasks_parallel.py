"""Chunk-distributed scan engine tasks.

The orchestrator discovers files (one Celery task per directory, bulk-inserting
pending rows), builds disjoint FCP path-range chunks, and fans them out as
process_chunk_task across all workers. Completion uses last-chunk-finalizes:
every chunk exit marks the chunk terminal and the last one finalizes the scan
under a row lock (exactly-once). The scheduler's stuck-scan sweeper is the
backstop if the winner dies between chunk-complete and finalize.
Celery-free engine logic lives in pixelprobe.services.scan_engine.

Liveness and recovery (issue #75): each chunk task runs a heartbeat thread
bumping ScanState.last_update, so the sweeper's 30-minute staleness rule only
fires on true crashes. When the heartbeat goes stale with chunk rows still
active (dead workers, lost queue), the sweeper calls
redispatch_orphaned_chunks to re-queue them. process_chunk_task returns
ALREADY_TERMINAL for duplicate deliveries against a finished chunk and
SUPERSEDED when its task id no longer matches the chunk's recorded owner.
"""

import logging
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from celery import current_task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import update

from pixelprobe.celery_config import celery_app
from pixelprobe.constants import SCAN_PHASES
from pixelprobe.models import (db, ScanState, ScanResult, ScanChunk, ScanRunFile,
                               ScanRunRoot, ScanTask, ScanConfiguration)
from pixelprobe.media_checker import PixelProbe, load_exclusions_with_patterns
from pixelprobe.progress_utils import clear_scan_progress_redis, update_scan_progress_redis
from pixelprobe.utils.integrity import apply_scan_baseline
from pixelprobe.utils.overrides import retire_stale_override
from pixelprobe.services.scan_engine import (
    build_scan_chunks, claim_scan_slot, finalize_scan,
    maybe_finalize_scan, sync_progress_from_chunks
)
from pixelprobe.services.scan_reporting import add_files_batch_to_db
from pixelprobe.utils.helpers import batch_process, env_int
from pixelprobe.utils.paths import is_path_under
from pixelprobe.utils.mounts import mount_matches_baseline
from pixelprobe.utils.security import (
    PathTraversalError, get_allowed_scan_paths, resolve_authorized_media_file,
)

logger = logging.getLogger(__name__)


# Discovery is the only scan task with a time limit: the incompleteness guard
# below depends on it. Default sized for multi-hour walks of 1M+ files on
# network storage; override via env for slower mounts.
DISCOVERY_TASK_TIMEOUT_SECS = env_int('DISCOVERY_TASK_TIMEOUT_SECS', 3600, floor=60)

_DISCOVERY_INSERT_BATCH = 500
_CHUNK_COMMIT_BATCH = 100
_PROGRESS_WRITE_INTERVAL_SECS = 60  # also write progress at least this often (stuck-sweeper safety)

# Liveness heartbeat: a chunk task can sit inside checker.scan_file() for
# 30-60+ minutes on one large movie, during which no progress write happens.
# The heartbeat keeps ScanState.last_update moving while the task is alive so
# the stuck-scan sweeper's 30-minute staleness rule only fires on true crashes
# (issue #75). Daemon thread: dies with the worker, which is the point.
_CHUNK_HEARTBEAT_INTERVAL_SECS = env_int('CHUNK_HEARTBEAT_INTERVAL_SECS', 120, floor=15)
TASK_DISPATCH_LEASE_MINUTES = 10


def _run_required_paths(scan_id):
    """Return the immutable roots recorded for a directory scan."""
    roots = ScanRunRoot.query.filter_by(scan_id=scan_id).all()
    return [root.resolved_path or os.path.realpath(root.root_path) for root in roots]


def _root_mount_baseline_matches(resolved):
    """Return false when an administrator-required storage baseline changed."""
    configs = ScanConfiguration.query.filter_by(is_active=True, require_mount=True).all()
    matches = [config for config in configs if config.path and is_path_under(
        resolved, os.path.realpath(config.path))]
    config = max(matches, key=lambda item: len(os.path.realpath(item.path)), default=None)
    if config is None:
        return True
    matched, _ = mount_matches_baseline(
        resolved, config.mount_filesystem_type, config.mount_source, config.mount_root)
    return matched


def _build_chunk_checker(scan_id):
    excluded_paths, excluded_extensions, excluded_patterns = load_exclusions_with_patterns()
    return PixelProbe(
        database_path=None,
        excluded_paths=excluded_paths,
        excluded_extensions=excluded_extensions,
        excluded_patterns=excluded_patterns,
        allowed_paths=get_allowed_scan_paths(),
        required_paths=_run_required_paths(scan_id) or None,
    )


def _claim_chunk_members(scan_id, first_path, last_path, checker):
    """Claim only members still permitted by the current policy."""
    members = (ScanRunFile.query.filter(
        ScanRunFile.scan_id == scan_id,
        ScanRunFile.file_path >= first_path,
        ScanRunFile.file_path <= last_path,
        ScanRunFile.status == 'pending',
    ).with_for_update().all())
    claimed_ids = []
    now = datetime.now(timezone.utc)
    for member in members:
        if not checker._is_supported_file(member.file_path):
            member.status = 'skipped'
            member.outcome = 'excluded'
            member.error_message = 'Excluded by current scan policy'
            member.completed_at = now
            continue
        try:
            resolve_authorized_media_file(
                member.file_path, checker.allowed_paths, checker.required_paths)
        except PathTraversalError:
            member.status = 'error'
            member.outcome = 'unavailable'
            member.error_message = 'File no longer permitted by scan root policy'
            member.completed_at = now
            continue
        member.status = 'processing'
        member.claimed_at = now
        if member.scan_result_id is not None:
            claimed_ids.append(member.scan_result_id)
    # Run members track queue activity; global results change after decoding.
    db.session.commit()
    return claimed_ids


def _set_scan_output(result, output):
    """Use ScanResult's configured rotation policy for run snapshots too."""
    result.scan_output = None
    result.append_output(str(output or ''))
    return result.scan_output


def _mark_task_running(scan_id, celery_task_id):
    if not celery_task_id:
        return True
    task = ScanTask.query.filter_by(scan_id=scan_id, celery_task_id=celery_task_id).with_for_update().first()
    if not task:
        db.session.commit()
        return True
    if task.status not in ('queued', 'dispatched'):
        db.session.commit()
        return False
    task.status = 'processing'
    task.dispatch_lease_expires_at = None
    task.error_message = None
    db.session.commit()
    return True


def _mark_task_finished(scan_id, celery_task_id, status='completed', error=None):
    if not celery_task_id:
        return
    task = ScanTask.query.filter_by(scan_id=scan_id, celery_task_id=celery_task_id).first()
    if task and task.status != 'cancelled':
        task.status = status
        task.error_message = str(error)[:1000] if error else None
        task.completed_at = datetime.now(timezone.utc)
        task.dispatch_lease_expires_at = None
        db.session.commit()


def _mark_task_retry(scan_id, celery_task_id, error):
    task = ScanTask.query.filter_by(scan_id=scan_id, celery_task_id=celery_task_id).first()
    if task and task.status == 'processing':
        task.status = 'dispatched'
        task.error_message = str(error)[:1000]
        task.dispatch_lease_expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=TASK_DISPATCH_LEASE_MINUTES)
        db.session.commit()


def _prepare_task_dispatch(task):
    if task.status not in ('queued', 'dispatched'):
        db.session.commit()
        return False
    now = datetime.now(timezone.utc)
    if (task.status == 'dispatched' and task.dispatch_lease_expires_at
            and task.dispatch_lease_expires_at > now):
        db.session.commit()
        return False
    task.status = 'dispatched'
    task.dispatch_attempts = (task.dispatch_attempts or 0) + 1
    task.dispatch_lease_expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=TASK_DISPATCH_LEASE_MINUTES)
    task.error_message = None
    db.session.commit()
    return True


def _record_dispatch_failure(task_id, error):
    task = db.session.get(ScanTask, task_id)
    if task and task.status == 'dispatched':
        task.status = 'queued'
        task.dispatch_lease_expires_at = None
        task.error_message = str(error)[:1000]
        db.session.commit()


def dispatch_scan_task_intent(task):
    """Publish one persisted scan task, retaining its intent on broker failure."""
    if not _prepare_task_dispatch(task):
        return False
    payload = task.payload or {}
    try:
        if task.purpose == 'orchestrator':
            parallel_scan_orchestrator.apply_async(kwargs=payload, task_id=task.celery_task_id)
        elif task.purpose == 'discovery':
            discover_directory_task.apply_async(
                args=(payload['path'], task.scan_id, payload.get('excluded_paths'),
                      payload.get('excluded_extensions'), payload.get('excluded_patterns')),
                task_id=task.celery_task_id)
        elif task.purpose == 'continuation':
            resume_scan_after_discovery.apply_async(
                args=(task.scan_id, bool(payload.get('force_rescan'))), task_id=task.celery_task_id)
        elif task.purpose == 'chunk':
            process_chunk_task.apply_async(
                args=(task.chunk_id, task.scan_id, bool(payload.get('force_rescan'))),
                task_id=task.celery_task_id)
        elif task.purpose == 'selected':
            from pixelprobe.tasks import scan_files_task
            scan_files_task.apply_async(kwargs=payload, task_id=task.celery_task_id)
        elif task.purpose == 'single':
            from pixelprobe.tasks import scan_media_task
            scan_media_task.apply_async(kwargs=payload, task_id=task.celery_task_id)
        else:
            raise ValueError(f'Unknown scan task purpose: {task.purpose}')
        return True
    except Exception as exc:
        db.session.rollback()
        _record_dispatch_failure(task.id, exc)
        logger.warning("Scan task dispatch deferred for %s: %s", task.id, type(exc).__name__)
        return False


def reconcile_scan_task_intents(limit=100):
    """Re-publish durable scan intents whose task has not become active."""
    now = datetime.now(timezone.utc)
    intents = ScanTask.query.filter(
        (ScanTask.status == 'queued') |
        ((ScanTask.status == 'dispatched') &
         ((ScanTask.dispatch_lease_expires_at <= now) |
          (ScanTask.dispatch_lease_expires_at.is_(None))))
    ).order_by(ScanTask.id).limit(limit).all()
    dispatched = 0
    for intent in intents:
        task = (ScanTask.query.filter_by(id=intent.id).populate_existing()
                .with_for_update().first())
        state = (ScanState.query.filter_by(scan_id=task.scan_id).populate_existing().first()
                 if task else None)
        if not task:
            db.session.commit()
            continue
        if (task.status == 'dispatched' and task.dispatch_lease_expires_at
                and task.dispatch_lease_expires_at > now):
            db.session.commit()
            continue
        if task.status not in ('queued', 'dispatched'):
            db.session.commit()
            continue
        if (not state or not state.is_active or state.phase in ('cancelled', 'completed', 'error',
                                                                 'crashed', 'interrupted')
                or task.generation != (state.dispatch_generation or 0)):
            task.status = 'cancelled'
            task.completed_at = now
            task.dispatch_lease_expires_at = None
            db.session.commit()
            continue
        if task.purpose == 'chunk':
            chunk = db.session.get(ScanChunk, task.chunk_id)
            if (not chunk or chunk.is_complete or chunk.celery_task_id != task.celery_task_id):
                task.status = 'cancelled'
                task.completed_at = now
                task.dispatch_lease_expires_at = None
                db.session.commit()
                continue
        if dispatch_scan_task_intent(task):
            dispatched += 1
    return dispatched


def recover_stale_processing_task_intents(scan_id):
    """Release non-chunk work whose worker died after claiming its intent.

    The scheduler calls this only after the run heartbeat is stale. Chunk
    recovery creates a new owner id instead, so a late chunk worker cannot
    write the revived chunk.
    """
    state = (ScanState.query.filter_by(scan_id=scan_id)
             .populate_existing().with_for_update().first())
    if (not state or not state.is_active or state.phase in ('cancelled', 'completed', 'error',
                                                            'crashed', 'interrupted')):
        db.session.commit()
        return 0
    intents = (ScanTask.query.filter(
        ScanTask.scan_id == scan_id,
        ScanTask.status == 'processing',
        ScanTask.purpose != 'chunk',
    ).populate_existing().with_for_update().all())
    recovered = 0
    for task in intents:
        if task.generation != (state.dispatch_generation or 0):
            task.status = 'cancelled'
            task.completed_at = datetime.now(timezone.utc)
            continue
        task.status = 'queued'
        task.dispatch_lease_expires_at = None
        task.error_message = 'Recovered after stale worker lease'
        recovered += 1
    db.session.commit()
    return recovered


def _heartbeat_once(app, scan_id: str) -> bool:
    """One liveness write on a fresh app-context session. True if a row updated.

    Runs on the heartbeat thread, never on the task's session. The is_active
    guard means a heartbeat can never resurrect a cancelled/crashed scan.
    """
    try:
        with app.app_context():
            result = db.session.execute(
                update(ScanState)
                .where(ScanState.scan_id == scan_id,
                       ScanState.is_active == True)
                .values(last_update=datetime.now(timezone.utc))
            )
            db.session.commit()
            return result.rowcount > 0
    except Exception as e:
        logger.warning(f"Chunk heartbeat write failed for scan {scan_id}: {e}")
        return False


def _start_chunk_heartbeat(app, scan_id: str, chunk_db_id: int,
                           interval: float = None) -> threading.Event:
    """Start a daemon thread bumping ScanState.last_update; returns stop event."""
    if interval is None:
        interval = _CHUNK_HEARTBEAT_INTERVAL_SECS
    stop = threading.Event()

    def beat():
        failures = 0
        while not stop.wait(interval):
            if _heartbeat_once(app, scan_id):
                failures = 0
            else:
                failures += 1
                if failures == 3:
                    # Also hit when the scan was cancelled (is_active False)
                    # and this thread has not been stopped yet - benign there
                    logger.warning(
                        f"Chunk {chunk_db_id} heartbeat has not updated scan "
                        f"{scan_id} for 3 consecutive intervals")

    thread = threading.Thread(target=beat, daemon=True,
                              name=f'chunk-heartbeat:{chunk_db_id}')
    thread.start()
    return stop


def _mark_chunk_terminal(chunk, status: str, files_scanned: int = None, error: str = None):
    chunk.status = status
    chunk.is_complete = True
    chunk.end_time = datetime.now(timezone.utc)
    if files_scanned is not None:
        chunk.files_scanned = files_scanned
    if error:
        chunk.error_message = str(error)[:1000]
    db.session.commit()


def _reclaim_chunk_range(scan_id: str, first_path: str, last_path: str):
    """Return unscanned claimed rows in a chunk's range to pending."""
    member_ids = [row[0] for row in db.session.query(ScanRunFile.scan_result_id).filter(
        ScanRunFile.scan_id == scan_id,
        ScanRunFile.file_path >= first_path,
        ScanRunFile.file_path <= last_path,
        ScanRunFile.status == 'processing',
    ).all()]
    ScanRunFile.query.filter(
        ScanRunFile.scan_id == scan_id,
        ScanRunFile.file_path >= first_path,
        ScanRunFile.file_path <= last_path,
        ScanRunFile.status == 'processing',
    ).update({'status': 'pending', 'claimed_at': None}, synchronize_session=False)
    # Clear only legacy global claims; new claims do not write this status.
    if member_ids:
        ScanResult.query.filter(ScanResult.id.in_(member_ids),
                                ScanResult.scan_status == 'scanning').update(
            {'scan_status': 'pending'}, synchronize_session=False)
    db.session.commit()


def _write_chunk_progress(chunk, files_scanned: int, scan_id: str, current_file: str = None,
                          celery_task_id: str = None):
    """Persist batch scan results + chunk/aggregate progress, mirror to Redis.

    The commit also persists the batch's ScanResult updates, so a commit
    failure must PROPAGATE: the task-level retry reclaims the chunk range and
    re-scans it. Swallowing it here would lose up to a batch of results while
    still counting them as scanned.
    """
    locked_chunk = ScanChunk.query.filter_by(id=chunk.id).with_for_update().first()
    scan_state = ScanState.query.filter_by(scan_id=scan_id).with_for_update().first()
    if (not locked_chunk or not scan_state or not scan_state.is_active
            or scan_state.phase != SCAN_PHASES['SCANNING']
            or (celery_task_id and locked_chunk.celery_task_id
                and locked_chunk.celery_task_id != celery_task_id)):
        db.session.commit()
        return False
    locked_chunk.files_scanned = files_scanned
    sync_progress_from_chunks(scan_state, scan_id)
    scan_state.last_update = datetime.now(timezone.utc)
    if current_file:
        scan_state.current_file = current_file
    db.session.commit()
    try:
        update_scan_progress_redis(
            scan_id,
            files_processed=scan_state.files_processed,
            estimated_total=scan_state.estimated_total,
            phase=scan_state.phase,
            current_file=os.path.basename(current_file) if current_file else ''
        )
    except Exception as e:
        logger.warning(f"Redis progress mirror failed for {scan_id}: {e}")
    return True


def _lock_chunk_result_write(scan_id, chunk_id, celery_task_id, scan_result_id):
    """Lock the post-decode persistence fence without holding it during IO."""
    state = ScanState.query.filter_by(scan_id=scan_id).with_for_update().first()
    chunk = ScanChunk.query.filter_by(id=chunk_id).with_for_update().first()
    member = (ScanRunFile.query.filter_by(scan_id=scan_id, scan_result_id=scan_result_id)
              .with_for_update().first())
    if not state or not chunk or not member:
        db.session.commit()
        return None
    if not state.is_active or state.phase != SCAN_PHASES['SCANNING']:
        db.session.commit()
        return 'cancelled'
    if ((chunk.celery_task_id and celery_task_id
         and chunk.celery_task_id != celery_task_id)
            or chunk.status != 'processing'):
        db.session.commit()
        return 'superseded'
    if member.status != 'processing':
        db.session.commit()
        return 'cancelled'
    return member


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60,
                 soft_time_limit=None, time_limit=None)
def process_chunk_task(self, chunk_db_id: int, scan_id: str, force_rescan: bool = False):
    """Scan one FCP path-range chunk. Every exit path marks the chunk terminal
    and runs the finalization check."""
    logger.info(f"Worker processing chunk {chunk_db_id} for scan {scan_id}")
    first_path = last_path = None
    hb_stop = None

    try:
        from flask import current_app

        if not _mark_task_running(scan_id, self.request.id):
            return {'status': 'SUPERSEDED', 'chunk_id': chunk_db_id}

        chunk = db.session.get(ScanChunk, chunk_db_id)
        if not chunk:
            logger.error(f"Chunk {chunk_db_id} not found")
            return {'status': 'ERROR', 'chunk_id': chunk_db_id, 'error': 'Chunk not found'}

        # Duplicate-delivery guards: a ghost redelivery (broker visibility
        # timeout) or a task superseded by chunk revival must not touch chunk
        # state. Without the terminal guard a ghost resets a completed chunk
        # to processing and zeroes files_scanned via the empty-claim path.
        if chunk.is_complete:
            logger.info(f"Chunk {chunk_db_id} already terminal, ignoring duplicate delivery")
            return {'status': 'ALREADY_TERMINAL', 'chunk_id': chunk_db_id}
        if chunk.celery_task_id and self.request.id and chunk.celery_task_id != self.request.id:
            logger.info(f"Chunk {chunk_db_id} owned by task {chunk.celery_task_id}, "
                        f"this delivery ({self.request.id}) is superseded")
            return {'status': 'SUPERSEDED', 'chunk_id': chunk_db_id}

        fcp = chunk.fcp_range()
        if not fcp:
            _mark_chunk_terminal(chunk, 'error', files_scanned=0,
                                 error=f'Invalid chunk range: {chunk.directory_path[:200]}')
            maybe_finalize_scan(scan_id)
            return {'status': 'ERROR', 'chunk_id': chunk_db_id, 'error': 'Invalid chunk range'}
        first_path, last_path = fcp

        # Prior attempts' tally (revival or worker-lost redelivery): this
        # attempt's counter starts at zero, so every accounting write below is
        # base + local count, or the aggregate freezes at the pre-revival
        # high-water mark (sync_progress_from_chunks never decreases) and the
        # final report undercounts. The stored tally is cross-checked against
        # files actually finished in-range since the scan started, which also
        # repairs tallies clobbered by pre-fix revivals.
        base_scanned = chunk.files_scanned or 0
        finished_members = ScanRunFile.query.filter(
            ScanRunFile.scan_id == scan_id,
            ScanRunFile.file_path >= first_path,
            ScanRunFile.file_path <= last_path,
            ScanRunFile.status.in_(['completed', 'error', 'unreadable', 'unsupported']),
        ).count()
        base_scanned = max(base_scanned, finished_members)

        chunk.status = 'processing'
        chunk.start_time = datetime.now(timezone.utc)
        db.session.commit()

        hb_stop = _start_chunk_heartbeat(current_app._get_current_object(),
                                         scan_id, chunk_db_id)

        # Re-read exclusions at execution time. Discovery may have happened
        # hours earlier, and an administrator can withdraw a path meanwhile.
        checker = _build_chunk_checker(scan_id)

        # Range ownership makes the claim race-free. Policy is evaluated in
        # Python because filename patterns and canonical descriptor guards are
        # not safely expressible as one portable SQL predicate.
        def claim_pending():
            return _claim_chunk_members(scan_id, first_path, last_path, checker)

        claimed_ids = claim_pending()

        if not claimed_ids:
            # A worker-lost redelivery arrives with the SAME task id (acks_late
            # + reject_on_worker_lost) while the dead attempt's rows are still
            # claimed as 'scanning'; without this reclaim-and-retry the chunk
            # would be marked completed with files_scanned=0 and those files
            # silently dropped from the scan
            _reclaim_chunk_range(scan_id, first_path, last_path)
            claimed_ids = claim_pending()

        if not claimed_ids:
            # Empty chunk: terminal, but never touch shared progress fields.
            # Keep the prior attempts' tally - zeroing it here after a revival
            # would erase real, committed work from the aggregate.
            logger.info(f"Chunk {chunk_db_id} has no pending files, marking complete")
            _mark_chunk_terminal(chunk, 'completed', files_scanned=base_scanned)
            maybe_finalize_scan(scan_id)
            _mark_task_finished(scan_id, self.request.id)
            return {'status': 'SKIPPED', 'chunk_id': chunk_db_id, 'files_processed': 0}

        logger.info(f"Chunk {chunk_db_id}: claimed {len(claimed_ids)} files")

        files_processed = 0
        files_corrupted = 0
        last_progress_write = time.time()

        for batch_ids in batch_process(claimed_ids, _CHUNK_COMMIT_BATCH):
            # Cancellation check once per batch
            is_active = db.session.query(ScanState.is_active).filter_by(scan_id=scan_id).scalar()
            if not is_active:
                logger.info(f"Chunk {chunk_db_id}: scan cancelled, stopping")
                _reclaim_chunk_range(scan_id, first_path, last_path)
                _mark_chunk_terminal(chunk, 'cancelled',
                                     files_scanned=base_scanned + files_processed)
                _mark_task_finished(scan_id, self.request.id, 'cancelled')
                return {'status': 'CANCELLED', 'chunk_id': chunk_db_id,
                        'files_processed': files_processed}

            batch_rows = ScanResult.query.filter(ScanResult.id.in_(batch_ids)).all()
            current_file = None

            for db_result in batch_rows:
                file_path = db_result.file_path
                try:
                    scan_result = checker.scan_file(file_path, force_rescan=force_rescan)

                    # Decoding happens outside locks. Before touching the
                    # global result row, fence the late result against the
                    # run state, chunk owner, and immutable member claim.
                    member = _lock_chunk_result_write(
                        scan_id, chunk_db_id, self.request.id, db_result.id)
                    if member == 'cancelled':
                        logger.info(f"Chunk {chunk_db_id}: result discarded after cancellation")
                        return {'status': 'CANCELLED', 'chunk_id': chunk_db_id,
                                'files_processed': files_processed}
                    if member == 'superseded' or member is None:
                        logger.info(f"Chunk {chunk_db_id}: late result is no longer owned")
                        return {'status': 'SUPERSEDED', 'chunk_id': chunk_db_id}
                    db.session.refresh(db_result)

                    if scan_result:
                        result_outcome = scan_result.get('outcome')
                        if result_outcome not in ('completed', 'unsupported', 'unreadable', 'error'):
                            result_outcome = ('error' if scan_result.get('scan_tool') == 'error'
                                              else 'completed')
                        if result_outcome == 'completed' and not scan_result.get('file_hash'):
                            result_outcome = 'error'
                        corruption_details = scan_result.get('corruption_details', '')
                        warning_details = scan_result.get('warning_details', '')
                        is_corrupted = scan_result.get('is_corrupted')
                        has_warnings = scan_result.get('has_warnings', False)

                        # A nonverification outcome is not a media verdict.
                        # Keep the checker result only for completed scans;
                        # diagnostic text such as "read failed" must never
                        # turn an unreadable file into corruption.
                        if result_outcome != 'completed':
                            is_corrupted = None
                        elif corruption_details:
                            details_lower = corruption_details.lower()
                            if 'warning' in details_lower:
                                has_warnings = True
                                if not warning_details:
                                    warning_details = corruption_details
                        if warning_details and not has_warnings:
                            has_warnings = True

                        # A mark-as-good excused one finding class on one
                        # version of the file; different content or a different
                        # class of problem retires it (history columns stay)
                        if retire_stale_override(db_result, scan_result.get('file_hash'),
                                                 corruption_details, warning_details):
                            logger.info(
                                f"Override retired for {file_path}: new findings "
                                f"outside its scope")

                        db_result.is_corrupted = is_corrupted
                        db_result.scan_status = result_outcome
                        db_result.scan_date = datetime.now(timezone.utc)
                        db_result.corruption_details = corruption_details
                        stored_output = _set_scan_output(
                            db_result, scan_result.get('scan_output', ''))
                        db_result.has_warnings = has_warnings
                        db_result.warning_details = warning_details
                        # Guarded baseline write: never overwrite a
                        # bitrot-suspected file's stored hash/mtime (this is
                        # the writer for all chunked scans, including the
                        # rescans Phase 3 of the integrity check queues up)
                        baseline_written = False
                        if result_outcome == 'completed':
                            baseline_written = apply_scan_baseline(
                                db_result, scan_result.get('file_hash'),
                                scan_result.get('last_modified'))
                            if not baseline_written:
                                logger.info(f"Preserving hash/mtime baseline for incomplete or suspect result: {file_path}")
                        db_result.scan_tool = scan_result.get('scan_tool', 'unknown')
                        db_result.scan_duration = scan_result.get('scan_duration')
                        if result_outcome == 'completed' and baseline_written:
                            db_result.file_size = scan_result.get('file_size', db_result.file_size)
                            db_result.file_type = scan_result.get('file_type', db_result.file_type)
                            db_result.file_exists = True
                        db_result.error_message = (str(corruption_details)[:1000]
                                                   if result_outcome in ('error', 'unreadable') else None)

                        member.status = result_outcome
                        member.outcome = result_outcome
                        member.file_hash = scan_result.get('file_hash')
                        member.file_size = scan_result.get('file_size')
                        member.last_modified = scan_result.get('last_modified')
                        member.is_corrupted = is_corrupted
                        member.has_warnings = has_warnings
                        member.corruption_details = corruption_details
                        member.warning_details = warning_details
                        member.file_type = scan_result.get('file_type')
                        member.scan_tool = scan_result.get('scan_tool')
                        member.scan_output = stored_output
                        member.marked_as_good = db_result.marked_as_good
                        member.error_message = (str(corruption_details)[:1000]
                                                if result_outcome in ('error', 'unreadable') else None)
                        member.completed_at = datetime.now(timezone.utc)

                        if is_corrupted:
                            files_corrupted += 1
                    else:
                        db_result.scan_status = 'error'
                        member.status = 'error'
                        member.outcome = 'no_result'
                        member.completed_at = datetime.now(timezone.utc)
                        db_result.error_message = 'Scanner returned no result'

                    # Commit each post-decode result while the persistence
                    # fence is held. Never keep that lock across the next IO.
                    db.session.commit()
                    files_processed += 1
                    current_file = file_path

                except Exception as e:
                    logger.error(f"Error scanning {file_path} in chunk {chunk_db_id}: {e}")
                    member = _lock_chunk_result_write(
                        scan_id, chunk_db_id, self.request.id, db_result.id)
                    if member == 'cancelled':
                        return {'status': 'CANCELLED', 'chunk_id': chunk_db_id,
                                'files_processed': files_processed}
                    if member == 'superseded' or member is None:
                        return {'status': 'SUPERSEDED', 'chunk_id': chunk_db_id}
                    db.session.refresh(db_result)
                    # Preserve already-scanned results in this batch (a rollback
                    # would force them through a full ffmpeg re-scan later)
                    try:
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    try:
                        row = db.session.get(ScanResult, db_result.id)
                        if row:
                            row.scan_status = 'error'
                            row.error_message = str(e)[:500]
                            member.status = 'error'
                            member.outcome = 'exception'
                            member.error_message = str(e)[:1000]
                            member.completed_at = datetime.now(timezone.utc)
                            db.session.commit()
                    except Exception as db_error:
                        logger.error(f"Failed to mark file as error: {db_error}")
                        db.session.rollback()
                    files_processed += 1
                    continue

                # Time-based progress write for slow large files (sweeper safety)
                if time.time() - last_progress_write > _PROGRESS_WRITE_INTERVAL_SECS:
                    if not _write_chunk_progress(chunk, base_scanned + files_processed,
                                                 scan_id, current_file, self.request.id):
                        return {'status': 'CANCELLED', 'chunk_id': chunk_db_id,
                                'files_processed': files_processed}
                    last_progress_write = time.time()

            if not _write_chunk_progress(chunk, base_scanned + files_processed,
                                         scan_id, current_file, self.request.id):
                return {'status': 'CANCELLED', 'chunk_id': chunk_db_id,
                        'files_processed': files_processed}
            last_progress_write = time.time()

            # Cosmetic task metadata: a result-backend blip must not abort a
            # chunk (real progress is already committed to the DB above)
            try:
                current_task.update_state(state='PROGRESS', meta={
                    'chunk_id': chunk_db_id,
                    'current': files_processed,
                    'total': len(claimed_ids),
                    'scan_id': scan_id,
                })
            except Exception as e:
                logger.debug(f"Chunk {chunk_db_id} progress state update failed (non-fatal): {e}")

        # Ownership re-check: if revival re-assigned this chunk while we ran
        # (only possible after 600s of failed heartbeats), the newer task owns
        # the terminal write and the finalize - exit without touching either
        db.session.expire(chunk)
        if chunk.celery_task_id and self.request.id and chunk.celery_task_id != self.request.id:
            logger.warning(f"Chunk {chunk_db_id} was re-assigned to task "
                           f"{chunk.celery_task_id} while this task ran; yielding")
            return {'status': 'SUPERSEDED', 'chunk_id': chunk_db_id}

        _mark_chunk_terminal(chunk, 'completed',
                             files_scanned=base_scanned + files_processed)
        logger.info(f"Chunk {chunk_db_id} completed: {files_processed} files "
                    f"this attempt ({base_scanned + files_processed} total), "
                    f"{files_corrupted} corrupted")
        maybe_finalize_scan(scan_id)
        _mark_task_finished(scan_id, self.request.id)

        return {
            'status': 'SUCCESS',
            'chunk_id': chunk_db_id,
            'files_processed': files_processed,
            'files_corrupted': files_corrupted,
            'completed_at': datetime.now(timezone.utc).isoformat()
        }

    except Exception as exc:
        logger.error(f"Chunk task {self.request.id} failed: {exc}")
        db.session.rollback()

        # Always reclaim claimed-but-unscanned rows: the retried attempt's
        # bulk claim selects only 'pending', so without this a retry would
        # find nothing and mark the chunk completed with 0 files
        try:
            if first_path and last_path:
                _reclaim_chunk_range(scan_id, first_path, last_path)
        except Exception as reclaim_error:
            logger.error(f"Chunk {chunk_db_id} reclaim failed: {reclaim_error}")
            db.session.rollback()

        if self.request.retries < self.max_retries:
            _mark_task_retry(scan_id, self.request.id, exc)
            raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))

        # Max retries: terminal error so finalization still fires, then re-raise
        try:
            chunk = db.session.get(ScanChunk, chunk_db_id)
            if chunk:
                _mark_chunk_terminal(chunk, 'error', error=exc)
            maybe_finalize_scan(scan_id)
            _mark_task_finished(scan_id, self.request.id, 'failed', exc)
        except Exception as cleanup_error:
            logger.error(f"Chunk {chunk_db_id} terminal-error cleanup failed: {cleanup_error}")
            db.session.rollback()
        raise exc

    finally:
        # Stop heartbeating on every exit, including the retry path (the 60s
        # retry countdown must not keep the old attempt's heartbeat alive)
        if hb_stop:
            hb_stop.set()


def redispatch_orphaned_chunks(scan_id: str, force_rescan: bool = False) -> int:
    """Reclaim and re-dispatch all non-terminal chunks of a scan whose workers
    are provably gone (caller has verified last_update staleness against the
    chunk heartbeat). Returns the number of chunks re-dispatched.

    The new celery_task_id is committed BEFORE dispatch so any ghost delivery
    of the old task returns SUPERSEDED instead of racing the revival.
    """
    chunks = ScanChunk.query.filter(
        ScanChunk.scan_id == scan_id,
        ScanChunk.status.in_(ScanChunk.ACTIVE_STATUSES)
    ).all()

    scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
    if not scan_state or not scan_state.is_active:
        return 0
    dispatched = 0
    for chunk in chunks:
        fcp = chunk.fcp_range()
        if not fcp:
            # Legacy directory-path chunks are not this engine's to re-dispatch
            continue
        try:
            if chunk.status == 'processing':
                _reclaim_chunk_range(scan_id, *fcp)
            new_id = str(uuid.uuid4())
            chunk.status = 'pending'
            chunk.celery_task_id = new_id
            chunk.start_time = None
            old_intent = ScanTask.query.filter_by(scan_id=scan_id, chunk_id=chunk.id).filter(
                ScanTask.status.in_(['queued', 'dispatched', 'processing'])).first()
            if old_intent:
                old_intent.status = 'cancelled'
                old_intent.completed_at = datetime.now(timezone.utc)
            intent = ScanTask(
                scan_id=scan_id, chunk_id=chunk.id, purpose='chunk', celery_task_id=new_id,
                generation=scan_state.dispatch_generation,
                payload={'force_rescan': bool(force_rescan)},
            )
            db.session.add(intent)
            db.session.commit()
            if dispatch_scan_task_intent(intent):
                dispatched += 1
        except Exception as e:
            # Chunk already committed as pending: the next sweep retries it;
            # keep going so one broker hiccup does not strand the other chunks
            logger.error(f"Failed to re-dispatch chunk {chunk.id} of scan {scan_id}: {e}")
            db.session.rollback()
            continue

    if dispatched:
        scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
        if scan_state:
            scan_state.last_update = datetime.now(timezone.utc)
            scan_state.progress_message = (
                f'Recovered after interruption: re-dispatched {dispatched} chunks')
            db.session.commit()
        logger.warning(f"Revived scan {scan_id}: re-dispatched {dispatched} orphaned chunks")

    return dispatched


@celery_app.task(bind=True, soft_time_limit=DISCOVERY_TASK_TIMEOUT_SECS,
                 time_limit=DISCOVERY_TASK_TIMEOUT_SECS + 120)
def discover_directory_task(self, directory: str, scan_id: str,
                            excluded_paths: List[str] = None,
                            excluded_extensions: List[str] = None,
                            excluded_patterns: List[str] = None):
    """Walk one directory and bulk-insert discovered media as pending rows.

    Returns counts only (never file lists -- 1M+ paths through the result
    backend would exhaust Redis). complete=False means truncated; the
    orchestrator aborts rather than report a partial scan as complete.
    """
    logger.info(f"Worker {self.request.id} discovering files in {directory}")
    if not _mark_task_running(scan_id, self.request.id):
        return {'status': 'SUPERSEDED', 'directory': directory}

    excluded_paths = excluded_paths or []
    excluded_extensions = excluded_extensions or []
    excluded_patterns = excluded_patterns or []

    requested_root = os.path.normpath(os.path.abspath(directory))
    canonical_root = os.path.realpath(requested_root)
    active_roots = get_allowed_scan_paths()
    root_error = None
    if requested_root != canonical_root:
        root_error = 'Requested scan root must not be a symlink'
    elif not any(is_path_under(canonical_root, os.path.realpath(root))
                 for root in active_roots):
        root_error = 'Requested scan root is outside active configuration'
    if root_error:
        root_record = ScanRunRoot.query.filter_by(scan_id=scan_id, root_path=directory).first()
        if root_record:
            root_record.status = 'incomplete'
            root_record.error_message = root_error
            root_record.completed_at = datetime.now(timezone.utc)
            db.session.commit()
        _maybe_resume_after_discovery(scan_id)
        _mark_task_finished(scan_id, self.request.id, 'failed', root_error)
        return {'directory': directory, 'files_checked': 0, 'files_inserted': 0,
                'complete': False, 'error': root_error}

    checker = PixelProbe(
        database_path=None,  # No DB connection needed for discovery
        excluded_paths=excluded_paths,
        excluded_extensions=excluded_extensions,
        excluded_patterns=excluded_patterns,
        allowed_paths=active_roots,
        required_paths=[canonical_root],
    )

    start_time = time.time()
    files_checked = 0
    files_inserted = 0
    buffer = []
    complete = True
    error = None

    def flush():
        nonlocal files_inserted
        if buffer:
            added, _ = add_files_batch_to_db(buffer, scan_id=scan_id)
            files_inserted += added
            buffer.clear()
            # Atomic increment: multiple discovery tasks update one row
            db.session.execute(
                update(ScanState)
                .where(ScanState.scan_id == scan_id)
                .values(discovery_count=ScanState.discovery_count + added,
                        last_update=datetime.now(timezone.utc),
                        progress_message=f'Discovering files... ({directory})')
            )
            db.session.commit()

    # Heartbeat: a sparse tree can walk for many minutes between flush()es
    # (only supported files fill the batch), which would trip the 30-minute
    # staleness rule while discovery is alive and inside its own time limit
    from flask import current_app
    hb_stop = _start_chunk_heartbeat(current_app._get_current_object(),
                                     scan_id, f'discovery:{directory}')
    try:
        def walk_error(exc):
            nonlocal complete, error
            complete = False
            error = str(exc)
            logger.error(f"Discovery cannot read {getattr(exc, 'filename', directory)}: {exc}")

        for root, dirs, files in os.walk(directory, onerror=walk_error):
            dirs[:] = [d for d in dirs if not any(
                is_path_under(os.path.join(root, d), exc) for exc in excluded_paths
            )]

            for file in files:
                files_checked += 1
                file_path = os.path.join(root, file)

                # os.walk reports symlink entries. Keep discovery scoped to
                # the requested physical root before a member row exists.
                if not is_path_under(os.path.realpath(file_path), canonical_root):
                    logger.warning('Discovery skipped a path resolving outside the requested root')
                    continue

                # _is_supported_file applies path, extension, and filename-
                # pattern exclusions (checker was built with all three)
                if not checker._is_supported_file(file_path):
                    continue

                try:
                    file_path = resolve_authorized_media_file(
                        file_path, active_roots, [canonical_root])
                except PathTraversalError:
                    logger.warning('Discovery skipped a file no longer authorized by root policy')
                    continue

                buffer.append(file_path)
                if len(buffer) >= _DISCOVERY_INSERT_BATCH:
                    flush()

        flush()

    except SoftTimeLimitExceeded:
        logger.warning(f"Discovery of {directory} timed out after {files_checked} files "
                       f"({DISCOVERY_TASK_TIMEOUT_SECS}s limit; raise DISCOVERY_TASK_TIMEOUT_SECS)")
        complete = False
        error = 'soft_time_limit'
    except Exception as e:
        logger.error(f"Error during directory walk of {directory}: {e}")
        db.session.rollback()
        complete = False
        error = str(e)
    finally:
        hb_stop.set()

    elapsed = time.time() - start_time
    logger.info(f"Discovery of {directory}: checked {files_checked}, inserted {files_inserted} "
                f"in {elapsed:.1f}s (complete={complete})")
    root_record = ScanRunRoot.query.filter_by(scan_id=scan_id, root_path=directory).first()
    if root_record:
        root_record.status = 'completed' if complete else 'incomplete'
        root_record.error_message = error
        root_record.discovered_count = files_inserted
        root_record.completed_at = datetime.now(timezone.utc)
        db.session.commit()
    _maybe_resume_after_discovery(scan_id)
    _mark_task_finished(scan_id, self.request.id)
    return {'directory': directory, 'files_checked': files_checked,
            'files_inserted': files_inserted, 'complete': complete, 'error': error}


def _dispatch_discovery_tasks(scan_id, paths, excluded_paths, excluded_extensions,
                              excluded_patterns, commit=True, publish=True):
    tasks = []
    for path in paths:
        root = (ScanRunRoot.query.filter_by(scan_id=scan_id, root_path=path)
                .with_for_update().first())
        if not root or root.status != 'pending':
            continue
        task_id = str(uuid.uuid4())
        root.status = 'dispatched'
        task = ScanTask(scan_id=scan_id, purpose='discovery', celery_task_id=task_id,
                        generation=ScanState.query.filter_by(scan_id=scan_id).first().dispatch_generation,
                        payload={'path': path, 'excluded_paths': excluded_paths,
                                 'excluded_extensions': excluded_extensions,
                                 'excluded_patterns': excluded_patterns})
        db.session.add(task)
        tasks.append(task)
    db.session.flush()
    if commit:
        db.session.commit()
    if publish:
        for task in tasks:
            dispatch_scan_task_intent(task)
    return tasks


def _maybe_resume_after_discovery(scan_id):
    state = ScanState.query.filter_by(scan_id=scan_id).with_for_update().first()
    if not state or not state.is_active or state.phase != SCAN_PHASES['DISCOVERING']:
        db.session.commit()
        return
    roots = ScanRunRoot.query.filter_by(scan_id=scan_id).all()
    if not roots or any(root.status in ('pending', 'dispatched') for root in roots):
        db.session.commit()
        return
    if any(root.status in ('unavailable', 'incomplete', 'error') for root in roots):
        # Finalize through the immutable membership/report path so an
        # incomplete root cannot bypass the transactional report/outbox.
        finalize_scan(state)
        return
    state.files_added = sum(root.discovered_count or 0 for root in roots)
    state.phase = SCAN_PHASES['ADDING']
    state.phase_number = 2
    state.progress_message = 'Preparing scan chunks...'
    state.last_update = datetime.now(timezone.utc)
    continuation = ScanTask(scan_id=scan_id, purpose='continuation',
                            celery_task_id=str(uuid.uuid4()),
                            generation=state.dispatch_generation,
                            payload={'force_rescan': bool(state.force_rescan)})
    db.session.add(continuation)
    db.session.commit()
    dispatch_scan_task_intent(continuation)


@celery_app.task(bind=True)
def resume_scan_after_discovery(self, scan_id, force_rescan=False):
    if not _mark_task_running(scan_id, self.request.id):
        return {'status': 'SUPERSEDED', 'scan_id': scan_id}
    state = ScanState.query.filter_by(scan_id=scan_id).with_for_update().first()
    if not state or not state.is_active or state.phase != SCAN_PHASES['ADDING']:
        db.session.commit()
        return {'status': 'SKIPPED', 'scan_id': scan_id}
    chunks = build_scan_chunks(scan_id, commit=False)
    state.phase = SCAN_PHASES['SCANNING']
    state.phase_number = 3
    state.estimated_total = sum(chunk['files_discovered'] for chunk in chunks)
    state.phase_total = state.estimated_total
    state.progress_message = f'Scanning {state.estimated_total} files in {len(chunks)} chunks...'
    mappings = []
    task_intents = []
    for chunk in chunks:
        task_id = str(uuid.uuid4())
        mappings.append({'id': chunk['id'], 'celery_task_id': task_id})
        task_intents.append(ScanTask(
            scan_id=scan_id, chunk_id=chunk['id'], purpose='chunk', celery_task_id=task_id,
            generation=state.dispatch_generation, payload={'force_rescan': bool(force_rescan)}))
    db.session.bulk_update_mappings(ScanChunk, mappings)
    db.session.add_all(task_intents)
    # Phase transition, chunk ownership, and every publishable intent become
    # durable together. A crash cannot strand SCANNING chunks without owners.
    db.session.commit()
    if not chunks:
        state = ScanState.query.filter_by(scan_id=scan_id).with_for_update().first()
        finalize_scan(state)
        _mark_task_finished(scan_id, self.request.id)
        return {'status': 'COMPLETED', 'scan_id': scan_id, 'total_files': 0}
    for task in task_intents:
        dispatch_scan_task_intent(task)
    _mark_task_finished(scan_id, self.request.id)
    return {'status': 'LAUNCHED', 'scan_id': scan_id, 'chunks_created': len(chunks)}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60,
                 soft_time_limit=None, time_limit=None)
def parallel_scan_orchestrator(self, scan_id: str, paths: List[str] = None,
                               scan_type: str = 'full', force_rescan: bool = False):
    """Orchestrate a directory scan: discover -> chunk -> fan out.

    scan_type: 'full' (discover + scan; 'parallel' is a legacy alias) or
    'pending' (scan existing pending rows only).
    """
    logger.info(f"Starting scan orchestrator for {scan_id}, type={scan_type}, force={force_rescan}")
    paths = paths or []
    if scan_type == 'parallel':  # legacy alias, identical to 'full'
        scan_type = 'full'

    try:
        if scan_type not in ('full', 'pending'):
            raise ValueError(f"Unknown scan type: {scan_type}")
        if scan_type == 'full' and not paths:
            raise ValueError("Paths required for full scan")

        scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
        if not scan_state:
            # Dispatched without a route-level claim (scan_media_task shim);
            # claim properly so a concurrent scan still conflicts.
            ok, err_payload, _ = claim_scan_slot(scan_id, scan_type)
            if not ok:
                logger.warning(f"Orchestrator could not claim scan slot for {scan_id}: {err_payload}")
                return {'status': 'CONFLICT', 'scan_id': scan_id, **(err_payload or {})}
            scan_state = ScanState.query.filter_by(scan_id=scan_id).first()

        if not _mark_task_running(scan_id, self.request.id):
            return {'status': 'SUPERSEDED', 'scan_id': scan_id}

        # acks_late may redeliver after discovery/chunks were dispatched. The
        # run row is the durable phase owner: a duplicate orchestrator must
        # never reset counters, delete chunks, or revive a cancelled run.
        if scan_state.phase in ('cancelled', 'completed', 'error', 'crashed', 'interrupted'):
            return {'status': 'TERMINAL', 'scan_id': scan_id, 'phase': scan_state.phase}
        if scan_state.phase == SCAN_PHASES['ADDING']:
            continuation = ScanTask.query.filter(
                ScanTask.scan_id == scan_id,
                ScanTask.purpose == 'continuation',
                ScanTask.status.in_(['queued', 'dispatched', 'processing']),
            ).first()
            if not continuation:
                continuation = ScanTask(
                    scan_id=scan_id, purpose='continuation', celery_task_id=str(uuid.uuid4()),
                    generation=scan_state.dispatch_generation,
                    payload={'force_rescan': bool(scan_state.force_rescan)},
                )
                db.session.add(continuation)
                db.session.commit()
                dispatch_scan_task_intent(continuation)
            return {'status': 'RECOVERY_DISPATCHED', 'scan_id': scan_id, 'phase': scan_state.phase}
        if scan_state.phase == SCAN_PHASES['DISCOVERING']:
            paths = json.loads(scan_state.directories or '[]')
            excluded_paths, excluded_extensions, excluded_patterns = load_exclusions_with_patterns()
            tasks = _dispatch_discovery_tasks(
                scan_id, paths, excluded_paths, excluded_extensions, excluded_patterns,
                commit=True, publish=False)
            for task in tasks:
                dispatch_scan_task_intent(task)
            return {'status': 'RECOVERY_DISPATCHED', 'scan_id': scan_id, 'phase': scan_state.phase}
        if scan_state.phase != SCAN_PHASES['INITIALIZING']:
            return {'status': 'ALREADY_STARTED', 'scan_id': scan_id, 'phase': scan_state.phase}

        scan_state.start_scan(paths, force_rescan, commit=False)  # phase='discovering', resets counters
        scan_state.celery_task_id = self.request.id
        scan_state.scan_type = scan_type
        scan_state.num_workers = env_int('CELERY_CONCURRENCY', 4, floor=1)
        scan_state.phase_number = 1
        scan_state.progress_message = 'Discovering files...'
        try:
            clear_scan_progress_redis(scan_id)
        except Exception:
            pass
        # Phase 1: persist roots and task ownership before dispatch. Discovery
        # is asynchronous: the orchestrator never joins a Celery group from a
        # worker, so one worker cannot be blocked behind another worker.
        if scan_type == 'full':
            unavailable = []
            for path in paths:
                resolved = os.path.realpath(path)
                root = ScanRunRoot(scan_id=scan_id, root_path=path,
                                   resolved_path=resolved, status='pending')
                db.session.add(root)
                if not os.path.isdir(resolved) or not os.access(resolved, os.R_OK | os.X_OK):
                    root.status = 'unavailable'
                    root.error_message = 'Root does not exist, is not a directory, or is unreadable'
                    root.completed_at = datetime.now(timezone.utc)
                    unavailable.append(path)
                    continue
                if not _root_mount_baseline_matches(resolved):
                    root.status = 'unavailable'
                    root.error_message = 'Required storage mount is unavailable or does not match its approved baseline'
                    root.completed_at = datetime.now(timezone.utc)
                    unavailable.append(path)
            excluded_paths, excluded_extensions, excluded_patterns = load_exclusions_with_patterns()
            tasks = _dispatch_discovery_tasks(
                scan_id, paths, excluded_paths, excluded_extensions, excluded_patterns,
                commit=False, publish=False)
            db.session.commit()
            for task in tasks:
                dispatch_scan_task_intent(task)
            _maybe_resume_after_discovery(scan_id)
            _mark_task_finished(scan_id, self.request.id)
            return {'status': 'DISCOVERY_DISPATCHED', 'scan_id': scan_id,
                    'roots': len(paths), 'task_id': self.request.id}

        # Phase 2: adding/chunking
        scan_state.phase = SCAN_PHASES['ADDING']
        scan_state.phase_number = 2
        scan_state.progress_message = 'Preparing scan chunks...'
        scan_state.last_update = datetime.now(timezone.utc)

        chunks = build_scan_chunks(scan_id, commit=False)  # list of {'id', 'files_discovered'}
        total_to_scan = sum(c['files_discovered'] for c in chunks)

        # Phase 3: fan out
        scan_state.phase = SCAN_PHASES['SCANNING']
        scan_state.phase_number = 3
        scan_state.estimated_total = total_to_scan
        scan_state.phase_total = total_to_scan
        scan_state.phase_current = 0
        scan_state.progress_message = f'Scanning {total_to_scan} files in {len(chunks)} chunks...'
        scan_state.last_update = datetime.now(timezone.utc)
        # Pre-assign task ids and commit BEFORE dispatch (cancellation support
        # and the ownership guard in process_chunk_task): a fast worker must
        # never observe a not-yet-written owner id, and post-dispatch writes
        # depended on result.children order matching the signatures
        id_mappings = []
        task_intents = []
        for chunk in chunks:
            task_id = str(uuid.uuid4())
            id_mappings.append({'id': chunk['id'], 'celery_task_id': task_id})
            task_intents.append(ScanTask(
                scan_id=scan_id, chunk_id=chunk['id'], purpose='chunk',
                celery_task_id=task_id, generation=scan_state.dispatch_generation,
                payload={'force_rescan': bool(force_rescan)}))
        db.session.bulk_update_mappings(ScanChunk, id_mappings)
        db.session.add_all(task_intents)
        # See resume_scan_after_discovery: state, chunks, and intents are one
        # recovery boundary.
        db.session.commit()
        if not chunks:
            logger.info(f"Scan {scan_id}: no files to scan, finalizing")
            scan_state = ScanState.query.filter_by(scan_id=scan_id).with_for_update().first()
            finalize_scan(scan_state)
            _mark_task_finished(scan_id, self.request.id)
            return {'status': 'COMPLETED', 'scan_id': scan_id, 'total_files': 0}
        for task in task_intents:
            dispatch_scan_task_intent(task)

        logger.info(f"Scan {scan_id}: launched {len(chunks)} chunk tasks for {total_to_scan} files")
        _mark_task_finished(scan_id, self.request.id)
        return {
            'status': 'LAUNCHED',
            'scan_id': scan_id,
            'total_files': total_to_scan,
            'chunks_created': len(chunks),
            'task_id': self.request.id,
        }

    except Exception as exc:
        logger.error(f"Scan orchestrator failed for {scan_id}: {exc}")
        db.session.rollback()
        try:
            scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
            if scan_state:
                scan_state.phase = SCAN_PHASES['CRASHED']
                scan_state.is_active = False
                scan_state.error_message = str(exc)[:1000]
                scan_state.end_time = datetime.now(timezone.utc)
                finalize_scan(scan_state)
            _mark_task_finished(scan_id, self.request.id, 'failed', exc)
        except Exception:
            db.session.rollback()
        raise exc

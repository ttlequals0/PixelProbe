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
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import List

from celery import current_task, group
from celery.exceptions import SoftTimeLimitExceeded
from celery.result import allow_join_result
from sqlalchemy import update

from pixelprobe.celery_config import celery_app
from pixelprobe.constants import SCAN_PHASES
from pixelprobe.models import db, ScanState, ScanResult, ScanChunk
from pixelprobe.media_checker import PixelProbe, load_exclusions_with_patterns
from pixelprobe.progress_utils import clear_scan_progress_redis, update_scan_progress_redis
from pixelprobe.utils.integrity import apply_scan_baseline
from pixelprobe.utils.overrides import retire_stale_override
from pixelprobe.services.scan_engine import (
    build_scan_chunks, claim_scan_slot, finalize_scan,
    maybe_finalize_scan, sync_progress_from_chunks
)
from pixelprobe.services.scan_reporting import create_scan_report, add_files_batch_to_db
from pixelprobe.utils.helpers import batch_process, env_int
from pixelprobe.utils.paths import is_path_under, like_prefix

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


def _reclaim_chunk_range(first_path: str, last_path: str):
    """Return unscanned claimed rows in a chunk's range to pending."""
    ScanResult.query.filter(
        ScanResult.file_path >= first_path,
        ScanResult.file_path <= last_path,
        ScanResult.scan_status == 'scanning'
    ).update({'scan_status': 'pending'}, synchronize_session=False)
    db.session.commit()


def _write_chunk_progress(chunk, files_scanned: int, scan_id: str, current_file: str = None):
    """Persist batch scan results + chunk/aggregate progress, mirror to Redis.

    The commit also persists the batch's ScanResult updates, so a commit
    failure must PROPAGATE: the task-level retry reclaims the chunk range and
    re-scans it. Swallowing it here would lose up to a batch of results while
    still counting them as scanned.
    """
    chunk.files_scanned = files_scanned
    scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
    if scan_state:
        sync_progress_from_chunks(scan_state, scan_id)
        scan_state.last_update = datetime.now(timezone.utc)
        if current_file:
            scan_state.current_file = current_file
    db.session.commit()
    if scan_state:
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
        scan_started = db.session.query(ScanState.start_time).filter_by(
            scan_id=scan_id).scalar()
        base_scanned = chunk.files_scanned or 0
        if scan_started is not None:
            finished_in_range = ScanResult.query.filter(
                ScanResult.file_path >= first_path,
                ScanResult.file_path <= last_path,
                ScanResult.scan_date >= scan_started,
            ).count()
            base_scanned = max(base_scanned, finished_in_range)

        chunk.status = 'processing'
        chunk.start_time = datetime.now(timezone.utc)
        db.session.commit()

        hb_stop = _start_chunk_heartbeat(current_app._get_current_object(),
                                         scan_id, chunk_db_id)

        # Bulk claim: one statement, race-free (ranges are disjoint by construction)
        def claim_pending():
            rows = db.session.execute(
                update(ScanResult)
                .where(ScanResult.file_path >= first_path,
                       ScanResult.file_path <= last_path,
                       ScanResult.scan_status == 'pending')
                .values(scan_status='scanning')
                .returning(ScanResult.id)
            ).fetchall()
            db.session.commit()
            return [row[0] for row in rows]

        claimed_ids = claim_pending()

        if not claimed_ids:
            # A worker-lost redelivery arrives with the SAME task id (acks_late
            # + reject_on_worker_lost) while the dead attempt's rows are still
            # claimed as 'scanning'; without this reclaim-and-retry the chunk
            # would be marked completed with files_scanned=0 and those files
            # silently dropped from the scan
            _reclaim_chunk_range(first_path, last_path)
            claimed_ids = claim_pending()

        if not claimed_ids:
            # Empty chunk: terminal, but never touch shared progress fields.
            # Keep the prior attempts' tally - zeroing it here after a revival
            # would erase real, committed work from the aggregate.
            logger.info(f"Chunk {chunk_db_id} has no pending files, marking complete")
            _mark_chunk_terminal(chunk, 'completed', files_scanned=base_scanned)
            maybe_finalize_scan(scan_id)
            return {'status': 'SKIPPED', 'chunk_id': chunk_db_id, 'files_processed': 0}

        logger.info(f"Chunk {chunk_db_id}: claimed {len(claimed_ids)} files")

        # database_path=None: this task persists the results itself; a DB-backed
        # checker would double-write every row through its own engine (and leak
        # one engine per chunk task)
        checker = PixelProbe(database_path=None)

        files_processed = 0
        files_corrupted = 0
        last_progress_write = time.time()

        for batch_ids in batch_process(claimed_ids, _CHUNK_COMMIT_BATCH):
            # Cancellation check once per batch
            is_active = db.session.query(ScanState.is_active).filter_by(scan_id=scan_id).scalar()
            if not is_active:
                logger.info(f"Chunk {chunk_db_id}: scan cancelled, stopping")
                _reclaim_chunk_range(first_path, last_path)
                _mark_chunk_terminal(chunk, 'cancelled',
                                     files_scanned=base_scanned + files_processed)
                return {'status': 'CANCELLED', 'chunk_id': chunk_db_id,
                        'files_processed': files_processed}

            batch_rows = ScanResult.query.filter(ScanResult.id.in_(batch_ids)).all()
            current_file = None

            for db_result in batch_rows:
                file_path = db_result.file_path
                try:
                    scan_result = checker.scan_file(file_path, force_rescan=force_rescan)

                    if scan_result:
                        corruption_details = scan_result.get('corruption_details', '')
                        warning_details = scan_result.get('warning_details', '')
                        is_corrupted = scan_result.get('is_corrupted', False)
                        has_warnings = scan_result.get('has_warnings', False)

                        # Classify: serious errors -> corrupted; "warning" text -> warning
                        if corruption_details:
                            details_lower = corruption_details.lower()
                            if any(err in details_lower for err in ['error', 'failed', 'no such file', 'corrupted']):
                                is_corrupted = True
                            elif 'warning' in details_lower:
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
                        db_result.scan_status = 'completed'
                        db_result.scan_date = datetime.now(timezone.utc)
                        db_result.corruption_details = corruption_details
                        db_result.scan_output = str(scan_result.get('scan_output', ''))[:10000]
                        db_result.has_warnings = has_warnings
                        db_result.warning_details = warning_details
                        # Guarded baseline write: never overwrite a
                        # bitrot-suspected file's stored hash/mtime (this is
                        # the writer for all chunked scans, including the
                        # rescans Phase 3 of the integrity check queues up)
                        if not apply_scan_baseline(db_result, scan_result.get('file_hash'),
                                                   scan_result.get('last_modified')):
                            logger.info(f"Preserving hash/mtime baseline for bitrot-suspected file: {file_path}")
                        db_result.scan_tool = scan_result.get('scan_tool', 'unknown')
                        db_result.scan_duration = scan_result.get('scan_duration')
                        db_result.file_size = scan_result.get('file_size', 0)
                        db_result.file_type = scan_result.get('file_type', 'unknown')

                        if is_corrupted:
                            files_corrupted += 1
                    else:
                        db_result.scan_status = 'error'
                        db_result.error_message = 'Scanner returned no result'

                    files_processed += 1
                    current_file = file_path

                except Exception as e:
                    logger.error(f"Error scanning {file_path} in chunk {chunk_db_id}: {e}")
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
                            db.session.commit()
                    except Exception as db_error:
                        logger.error(f"Failed to mark file as error: {db_error}")
                        db.session.rollback()
                    files_processed += 1
                    continue

                # Time-based progress write for slow large files (sweeper safety)
                if time.time() - last_progress_write > _PROGRESS_WRITE_INTERVAL_SECS:
                    _write_chunk_progress(chunk, base_scanned + files_processed,
                                          scan_id, current_file)
                    last_progress_write = time.time()

            _write_chunk_progress(chunk, base_scanned + files_processed, scan_id, current_file)
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
                _reclaim_chunk_range(first_path, last_path)
        except Exception as reclaim_error:
            logger.error(f"Chunk {chunk_db_id} reclaim failed: {reclaim_error}")
            db.session.rollback()

        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))

        # Max retries: terminal error so finalization still fires, then re-raise
        try:
            chunk = db.session.get(ScanChunk, chunk_db_id)
            if chunk:
                _mark_chunk_terminal(chunk, 'error', error=exc)
            maybe_finalize_scan(scan_id)
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

    dispatched = 0
    for chunk in chunks:
        fcp = chunk.fcp_range()
        if not fcp:
            # Legacy directory-path chunks are not this engine's to re-dispatch
            continue
        try:
            if chunk.status == 'processing':
                _reclaim_chunk_range(*fcp)
            new_id = str(uuid.uuid4())
            chunk.status = 'pending'
            chunk.celery_task_id = new_id
            chunk.start_time = None
            db.session.commit()
            process_chunk_task.apply_async(
                args=(chunk.id, scan_id, force_rescan), task_id=new_id)
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

    excluded_paths = excluded_paths or []
    excluded_extensions = excluded_extensions or []
    excluded_patterns = excluded_patterns or []

    checker = PixelProbe(
        database_path=None,  # No DB connection needed for discovery
        excluded_paths=excluded_paths,
        excluded_extensions=excluded_extensions,
        excluded_patterns=excluded_patterns,
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
            added, _ = add_files_batch_to_db(buffer)
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
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not any(
                is_path_under(os.path.join(root, d), exc) for exc in excluded_paths
            )]

            for file in files:
                files_checked += 1
                file_path = os.path.join(root, file)

                # _is_supported_file applies path, extension, and filename-
                # pattern exclusions (checker was built with all three)
                if not checker._is_supported_file(file_path):
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
    return {'directory': directory, 'files_checked': files_checked,
            'files_inserted': files_inserted, 'complete': complete, 'error': error}


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

        scan_state.start_scan(paths, force_rescan)  # phase='discovering', resets counters
        scan_state.celery_task_id = self.request.id
        scan_state.scan_type = scan_type
        scan_state.num_workers = env_int('CELERY_CONCURRENCY', 4, floor=1)
        scan_state.phase_number = 1
        scan_state.progress_message = 'Discovering files...'
        db.session.commit()

        try:
            clear_scan_progress_redis(scan_id)
        except Exception:
            pass
        ScanChunk.query.filter_by(scan_id=scan_id).delete(synchronize_session=False)
        db.session.commit()

        files_added = 0

        # Phase 1: discovery (one task per directory, counts only)
        if scan_type == 'full':
            excluded_paths, excluded_extensions, excluded_patterns = load_exclusions_with_patterns()
            discovery_tasks = [
                discover_directory_task.s(path, scan_id, excluded_paths,
                                          excluded_extensions, excluded_patterns)
                for path in paths if os.path.exists(path)
            ]

            if discovery_tasks:
                logger.info(f"Launching {len(discovery_tasks)} discovery tasks")
                result = group(discovery_tasks).apply_async()

                discovery_timeout = env_int('DISCOVERY_RESULT_TIMEOUT_SECS', 7200, floor=60)
                discovery_incomplete = []
                # allow_join_result: Celery forbids result.get() inside a task
                # by default (prefork raises RuntimeError). Blocking one slot
                # for discovery is the accepted price of the incompleteness
                # guard's harvest logic.
                with allow_join_result():
                    try:
                        for res in result.get(timeout=discovery_timeout):
                            if not res:
                                discovery_incomplete.append('unreadable-discovery-result')
                                continue
                            files_added += res.get('files_inserted', 0)
                            if not res.get('complete', False):
                                discovery_incomplete.append(res.get('directory', '?'))
                    except Exception as e:
                        # Harvest what finished; only genuinely unfinished tasks count as incomplete
                        logger.error(f"Error getting discovery results: {e}")
                        for task_result in result.results:
                            try:
                                if task_result and task_result.ready():
                                    res = task_result.get(timeout=1)
                                    if res:
                                        files_added += res.get('files_inserted', 0)
                                        if not res.get('complete', False):
                                            discovery_incomplete.append(res.get('directory', '?'))
                                    else:
                                        discovery_incomplete.append('unreadable-discovery-result')
                                else:
                                    discovery_incomplete.append('discovery-task-unfinished')
                            except Exception:
                                discovery_incomplete.append('discovery-task-error')

                # Never scan (and report 'completed') on a truncated file set
                if discovery_incomplete:
                    msg = (f"Discovery incomplete for {len(discovery_incomplete)} target(s): "
                           f"{discovery_incomplete[:5]} - aborting so a partial file set is not "
                           f"reported as complete. Re-run the scan.")
                    logger.error(msg)
                    scan_state.error_scan(msg[:1000])
                    create_scan_report(scan_state)
                    return {'status': 'error', 'scan_id': scan_id,
                            'error': 'discovery_incomplete',
                            'incomplete_targets': discovery_incomplete}

            scan_state.files_added = files_added

        # Phase 2: adding/chunking
        scan_state.phase = SCAN_PHASES['ADDING']
        scan_state.phase_number = 2
        scan_state.progress_message = 'Preparing scan chunks...'
        scan_state.last_update = datetime.now(timezone.utc)
        db.session.commit()

        if force_rescan and paths:
            # Scoped to requested directories (never a DB-wide reset)
            for d in paths:
                ScanResult.query.filter(
                    ScanResult.scan_status.in_(['completed', 'error', 'scanning']),
                    ScanResult.file_path.like(like_prefix(d), escape='\\')
                ).update({'scan_status': 'pending'}, synchronize_session=False)
            db.session.commit()

        chunks = build_scan_chunks(scan_id)  # list of {'id', 'files_discovered'}
        total_to_scan = sum(c['files_discovered'] for c in chunks)

        # Phase 3: fan out
        scan_state.phase = SCAN_PHASES['SCANNING']
        scan_state.phase_number = 3
        scan_state.estimated_total = total_to_scan
        scan_state.phase_total = total_to_scan
        scan_state.phase_current = 0
        scan_state.progress_message = f'Scanning {total_to_scan} files in {len(chunks)} chunks...'
        scan_state.last_update = datetime.now(timezone.utc)
        db.session.commit()

        if not chunks:
            # Zero-chunk scans bypass maybe_finalize_scan (it requires chunks
            # so it can never finalize a live legacy-engine scan)
            logger.info(f"Scan {scan_id}: no files to scan, finalizing")
            scan_state = ScanState.query.filter_by(scan_id=scan_id).with_for_update().first()
            finalize_scan(scan_state)
            return {'status': 'COMPLETED', 'scan_id': scan_id, 'total_files': 0}

        # Pre-assign task ids and commit BEFORE dispatch (cancellation support
        # and the ownership guard in process_chunk_task): a fast worker must
        # never observe a not-yet-written owner id, and post-dispatch writes
        # depended on result.children order matching the signatures
        signatures = []
        id_mappings = []
        for chunk in chunks:
            task_id = str(uuid.uuid4())
            id_mappings.append({'id': chunk['id'], 'celery_task_id': task_id})
            signatures.append(
                process_chunk_task.s(chunk['id'], scan_id, force_rescan)
                .set(task_id=task_id))
        db.session.bulk_update_mappings(ScanChunk, id_mappings)
        db.session.commit()

        group(signatures).apply_async()

        logger.info(f"Scan {scan_id}: launched {len(chunks)} chunk tasks for {total_to_scan} files")
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
                db.session.commit()
                # Failed report so scheduled scans send the healthcheck failure ping
                create_scan_report(scan_state)
        except Exception:
            db.session.rollback()
        raise exc

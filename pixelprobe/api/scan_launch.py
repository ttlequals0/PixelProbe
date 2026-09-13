"""Scan-launch helper for the API blueprints.

Validation must happen BEFORE the claim; any failure after a claim must
release it or the next scan 409s until the stuck-scan sweeper. The claim and
release primitives live in the service layer (scan_engine) so the orchestrator
and tests can use them too.
"""
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from pixelprobe.services.scan_engine import claim_scan_slot, release_scan_claim
from pixelprobe.utils.celery_utils import check_celery_available

logger = logging.getLogger(__name__)

# Exact format the scheduler generates: scheduled_{schedule_id}_{YYYYMMDD_HHMMSS}
_SCHEDULED_SOURCE_RE = re.compile(r'^scheduled_(\d+)_(\d{8}_\d{6})$')


def launch_directory_scan(validated_dirs, force_rescan=False, source=None, scan_type='full'):
    """Claim the scan slot and dispatch the chunk-distributed orchestrator.

    Returns (payload, status_code). Callers must pass already-validated dirs.
    """
    # Scheduled scans carry identity in scan_id: scheduled_{id}_{ts}. The
    # source is user-suppliable JSON, so allowlist it against the scheduler's
    # exact format and rebuild the id from the match (anything else gets a
    # UUID instead of an attacker-chosen scan_id echoed into responses,
    # reports, and healthcheck routing).
    m = _SCHEDULED_SOURCE_RE.fullmatch(source) if source else None
    if m:
        scan_id = f"scheduled_{m.group(1)}_{m.group(2)}"
        logger.info(f"Using scheduled scan source as scan_id: {scan_id}")
    elif source == 'scheduled_periodic':  # default periodic scan label
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        scan_id = f'scheduled_periodic_{timestamp}_{uuid4().hex}'
    else:
        scan_id = str(uuid4())

    ok, err_payload, err_status = claim_scan_slot(scan_id, scan_type)
    if not ok:
        return err_payload, err_status

    if not check_celery_available():
        release_scan_claim(scan_id)
        return {
            'error': 'Celery workers not available',
            'message': 'Scanning requires Celery workers to be running'
        }, 503

    from pixelprobe.models import db, ScanState, ScanTask
    task_id = str(uuid4())
    state = ScanState.query.filter_by(scan_id=scan_id).with_for_update().first()
    intent = ScanTask(
        scan_id=scan_id, purpose='orchestrator', celery_task_id=task_id,
        generation=state.dispatch_generation,
        payload={'scan_id': scan_id, 'paths': validated_dirs, 'scan_type': scan_type,
                 'force_rescan': bool(force_rescan)},
    )
    state.celery_task_id = task_id
    db.session.add(intent)
    db.session.commit()

    # Lazy import: tasks_parallel -> celery_config -> app -> blueprints (circular)
    from pixelprobe.tasks_parallel import dispatch_scan_task_intent
    if not dispatch_scan_task_intent(intent):
        return {'status': 'pending_dispatch', 'scan_id': scan_id, 'task_id': task_id,
                'message': 'Scan request saved. Dispatch will retry automatically.'}, 202

    logger.info(f"Queued scan orchestrator {task_id} for scan_id {scan_id}")
    return {
        'status': 'queued',
        'scan_id': scan_id,
        'task_id': task_id,
        'message': 'Scan queued',
        'celery_enabled': True
    }, 200

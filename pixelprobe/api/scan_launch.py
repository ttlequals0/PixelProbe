"""Scan-launch helper for the API blueprints.

Validation must happen BEFORE the claim; any failure after a claim must
release it or the next scan 409s until the stuck-scan sweeper. The claim and
release primitives live in the service layer (scan_engine) so the orchestrator
and tests can use them too.
"""
import logging
from uuid import uuid4

from pixelprobe.services.scan_engine import claim_scan_slot, release_scan_claim
from pixelprobe.utils.celery_utils import check_celery_available

logger = logging.getLogger(__name__)


def launch_directory_scan(validated_dirs, force_rescan=False, source=None, scan_type='full'):
    """Claim the scan slot and dispatch the chunk-distributed orchestrator.

    Returns (payload, status_code). Callers must pass already-validated dirs.
    """
    # Scheduled scans carry identity in scan_id: scheduled_{id}_{ts}
    if source and source.startswith('scheduled_'):
        scan_id = source
        logger.info(f"Using scheduled scan source as scan_id: {scan_id}")
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

    try:
        # Lazy import: tasks_parallel -> celery_config -> app -> blueprints (circular)
        from pixelprobe.tasks_parallel import parallel_scan_orchestrator
        task = parallel_scan_orchestrator.delay(
            scan_id=scan_id,
            paths=validated_dirs,
            scan_type=scan_type,
            force_rescan=force_rescan
        )
    except Exception as e:
        logger.error(f"Failed to dispatch scan orchestrator: {e}", exc_info=True)
        release_scan_claim(scan_id)
        return {'error': 'Failed to launch scan'}, 500

    logger.info(f"Queued scan orchestrator {task.id} for scan_id {scan_id}")
    return {
        'status': 'queued',
        'scan_id': scan_id,
        'task_id': task.id,
        'message': 'Scan queued successfully using Celery task queue',
        'celery_enabled': True
    }, 200

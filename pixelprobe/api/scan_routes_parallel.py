"""
Enhanced parallel scan endpoint that distributes work across all Celery workers

This module provides an improved scan endpoint that properly utilizes all
available Celery workers instead of creating a single monolithic task.
"""

from flask import Blueprint, request
import logging

from pixelprobe.utils.security import validate_directory_path, AuditLogger, PathTraversalError, validate_json_input
from pixelprobe.utils.rate_limiting import rate_limit
from pixelprobe.auth import auth_required
from pixelprobe.api.scan_launch import launch_directory_scan

logger = logging.getLogger(__name__)

parallel_scan_bp = Blueprint('parallel_scan', __name__, url_prefix='/api')


@parallel_scan_bp.route('/scan-parallel', methods=['POST'])
@rate_limit("2 per minute")
@auth_required
@validate_json_input({
    'directories': {'required': True, 'type': list},
    'force_rescan': {'required': False, 'type': bool}
})
def scan_parallel():
    """DEPRECATED alias of /api/scan: both run the chunk-distributed engine.
    Kept for API compatibility; will be removed in a future major release."""
    data = request.get_json()
    directories = data['directories']
    force_rescan = data.get('force_rescan', False)

    validated_dirs = []
    for directory in directories:
        try:
            validated_path = validate_directory_path(directory)
            validated_dirs.append(validated_path)
            AuditLogger.log_action('scan_directory', {'directory': validated_path})
        except PathTraversalError as e:
            AuditLogger.log_security_event('path_traversal_attempt', str(e), 'warning')
            return {'error': f'Invalid directory path: {directory}'}, 400

    if not validated_dirs:
        return {'error': 'No valid directories to scan'}, 400

    payload, status = launch_directory_scan(validated_dirs, force_rescan=force_rescan,
                                            scan_type='parallel')
    if status == 200:
        payload['status'] = 'launched'  # legacy response shape for this endpoint
        payload['scan_type'] = 'parallel_v2'
        payload['force_rescan'] = force_rescan
        payload['directories'] = validated_dirs
    return payload, status


@parallel_scan_bp.route('/scan-parallel/status/<scan_id>', methods=['GET'])
@auth_required
def get_parallel_scan_status(scan_id):
    """
    Get status of a parallel scan including chunk progress
    
    Returns detailed information about:
    - Overall scan progress
    - Number of chunks being processed
    - Number of active workers
    - Files processed per worker
    """
    try:
        from pixelprobe.models import ScanState, ScanChunk
        from celery import current_app as celery_app
        
        # Get scan state
        scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
        if not scan_state:
            return {'error': 'Scan not found'}, 404
        
        # Get chunk statistics
        total_chunks = ScanChunk.query.filter_by(scan_id=scan_id).count()
        complete_chunks = ScanChunk.query.filter_by(
            scan_id=scan_id,
            is_complete=True
        ).count()
        
        # Get active tasks for this scan
        active_tasks = celery_app.control.inspect().active()
        scan_tasks = []
        
        if active_tasks:
            for worker, tasks in active_tasks.items():
                for task in tasks:
                    if 'scan_id' in task.get('kwargs', {}) and \
                       task['kwargs']['scan_id'] == scan_id:
                        scan_tasks.append({
                            'worker': worker,
                            'task_id': task['id'],
                            'name': task['name'],
                            'args': task.get('args', []),
                            'kwargs': task.get('kwargs', {})
                        })
        
        # Calculate progress
        progress_percent = (complete_chunks / total_chunks * 100) if total_chunks > 0 else 0

        return {
            'scan_id': scan_id,
            'phase': scan_state.phase,
            'is_active': scan_state.is_active,
            'progress_percent': round(progress_percent, 2),
            'chunks': {
                'total': total_chunks,
                'complete': complete_chunks,
                'remaining': total_chunks - complete_chunks
            },
            'active_workers': len(scan_tasks),
            'active_tasks': scan_tasks,
            'files_processed': scan_state.files_processed or 0,
            'estimated_total': scan_state.estimated_total or 0,
            'start_time': scan_state.start_time.isoformat() if scan_state.start_time else None,
            'message': scan_state.progress_message or f'Processing {complete_chunks}/{total_chunks} chunks'
        }

    except Exception as e:
        logger.error(f"Error getting parallel scan status: {e}", exc_info=True)
        return {'error': 'Failed to get scan status'}, 500


@parallel_scan_bp.route('/scan-parallel/workers', methods=['GET'])
@auth_required
def get_worker_status():
    """
    Get detailed status of all Celery workers
    
    Returns information about:
    - Number of workers available
    - Tasks assigned to each worker
    - Worker utilization
    """
    try:
        from celery import current_app as celery_app
        
        inspect = celery_app.control.inspect()
        
        # Get worker stats
        stats = inspect.stats()
        active = inspect.active()
        registered = inspect.registered()
        
        if not stats:
            return {
                'status': 'offline',
                'message': 'No Celery workers available'
            }

        worker_info = []
        total_processes = 0

        for worker_name, worker_stats in stats.items():
            pool_info = worker_stats.get('pool', {})
            processes = pool_info.get('max-concurrency', 0)
            total_processes += processes

            # Get active tasks for this worker
            active_tasks = active.get(worker_name, [])

            worker_info.append({
                'name': worker_name,
                'processes': processes,
                'active_tasks': len(active_tasks),
                'utilization': round(len(active_tasks) / processes * 100, 2) if processes > 0 else 0,
                'pool_type': pool_info.get('implementation', 'unknown'),
                'tasks': [task['name'] for task in active_tasks]
            })

        return {
            'status': 'online',
            'total_workers': total_processes,
            'worker_nodes': len(stats),
            'workers': worker_info,
            'registered_tasks': registered.get(list(stats.keys())[0], []) if stats else []
        }

    except Exception as e:
        logger.error(f"Error getting worker status: {e}", exc_info=True)
        return {'error': 'Failed to get worker status'}, 500
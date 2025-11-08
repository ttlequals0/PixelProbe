"""
Enhanced parallel scan endpoint that distributes work across all Celery workers

This module provides an improved scan endpoint that properly utilizes all
available Celery workers instead of creating a single monolithic task.
"""

from flask import Blueprint, request, jsonify, current_app
from uuid import uuid4
import logging

from pixelprobe.utils.security import validate_directory_path, AuditLogger, PathTraversalError, validate_json_input
from pixelprobe.utils.rate_limiting import rate_limit
from auth import auth_required

logger = logging.getLogger(__name__)

parallel_scan_bp = Blueprint('parallel_scan', __name__, url_prefix='/api')


def check_celery_available():
    """Check if Celery is available and configured"""
    try:
        if not hasattr(current_app, 'celery'):
            return False
        
        # Check if broker URL is configured
        broker_url = current_app.config.get('CELERY_BROKER_URL')
        if not broker_url:
            return False
        
        # Try to get worker stats
        from celery import current_app as celery_app
        stats = celery_app.control.inspect().stats()
        return stats is not None and len(stats) > 0
        
    except Exception as e:
        logger.debug(f"Celery not available: {e}")
        return False


@parallel_scan_bp.route('/scan-parallel', methods=['POST'])
@rate_limit("2 per minute")
@auth_required
@validate_json_input({
    'directories': {'required': True, 'type': list},
    'force_rescan': {'required': False, 'type': bool}
})
def scan_parallel():
    """
    Enhanced parallel scan endpoint that distributes chunks across all workers
    
    This endpoint uses the new parallel_scan_orchestrator task which:
    1. Discovers all files to scan
    2. Creates manageable chunks (~1000 files each)
    3. Spawns parallel tasks for each chunk
    4. Distributes work across ALL available Celery workers
    
    Returns:
        JSON response with scan status and task information
    """
    data = request.get_json()
    directories = data['directories']
    force_rescan = data.get('force_rescan', False)
    
    # Validate directories
    validated_dirs = []
    for directory in directories:
        try:
            validated_path = validate_directory_path(directory)
            validated_dirs.append(validated_path)
            AuditLogger.log_action('scan_directory', {'directory': validated_path})
        except PathTraversalError as e:
            AuditLogger.log_security_event('path_traversal_attempt', str(e), 'warning')
            return jsonify({'error': f'Invalid directory path: {directory}'}), 400
    
    if not validated_dirs:
        return jsonify({'error': 'No valid directories to scan'}), 400
    
    # Check if Celery is available
    if not check_celery_available():
        return jsonify({
            'error': 'Celery workers not available',
            'message': 'Parallel scanning requires Celery workers to be running'
        }), 503
    
    try:
        from pixelprobe.tasks_parallel import parallel_scan_orchestrator
        
        # Generate unique scan ID
        scan_id = str(uuid4())
        
        # Launch the parallel scan orchestrator
        task = parallel_scan_orchestrator.delay(
            scan_id=scan_id,
            paths=validated_dirs,
            force_rescan=force_rescan
        )
        
        logger.info(f"Launched parallel scan orchestrator {task.id} for scan {scan_id}")
        
        # Get worker count for informational purposes
        from celery import current_app as celery_app
        stats = celery_app.control.inspect().stats()
        total_workers = sum(len(worker_stats.get('pool', {}).get('processes', [])) 
                           for worker_stats in stats.values()) if stats else 0
        
        return jsonify({
            'status': 'launched',
            'scan_id': scan_id,
            'task_id': task.id,
            'message': f'Parallel scan launched - will distribute work across {total_workers} workers',
            'celery_workers': total_workers,
            'scan_type': 'parallel_v2',
            'force_rescan': force_rescan,
            'directories': validated_dirs
        })
        
    except ImportError as e:
        logger.error(f"Failed to import parallel tasks: {e}")
        return jsonify({
            'error': 'Parallel scanning module not available',
            'details': str(e)
        }), 500
    except Exception as e:
        logger.error(f"Failed to launch parallel scan: {e}")
        return jsonify({
            'error': 'Failed to launch parallel scan',
            'details': str(e)
        }), 500


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
        from models import ScanState, ScanChunk
        from celery import current_app as celery_app
        
        # Get scan state
        scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
        if not scan_state:
            return jsonify({'error': 'Scan not found'}), 404
        
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
        
        return jsonify({
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
        })
        
    except Exception as e:
        logger.error(f"Error getting parallel scan status: {e}")
        return jsonify({
            'error': 'Failed to get scan status',
            'details': str(e)
        }), 500


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
            return jsonify({
                'status': 'offline',
                'message': 'No Celery workers available'
            })
        
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
        
        return jsonify({
            'status': 'online',
            'total_workers': total_processes,
            'worker_nodes': len(stats),
            'workers': worker_info,
            'registered_tasks': registered.get(list(stats.keys())[0], []) if stats else []
        })
        
    except Exception as e:
        logger.error(f"Error getting worker status: {e}")
        return jsonify({
            'error': 'Failed to get worker status',
            'details': str(e)
        }), 500
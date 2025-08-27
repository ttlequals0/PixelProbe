"""
OpenAPI/Swagger route implementations
"""

from flask import request, jsonify, current_app
from flask_restx import Resource
from pixelprobe.api.swagger import (
    api, scan_ns, stats_ns, maintenance_ns, admin_ns, export_ns,
    scan_directories_model, scan_status_model, stats_summary_model,
    file_changes_model, cleanup_status_model, export_request_model,
    config_model, schedule_model, error_model, success_model,
    reset_for_rescan_model, reset_result_model, reset_by_path_model,
    stuck_scan_recovery_model, parallel_scan_model, parallel_scan_response_model,
    reset_incomplete_scans_model
)
import logging

logger = logging.getLogger(__name__)

# Scan endpoints
@scan_ns.route('/directories')
class ScanDirectories(Resource):
    @scan_ns.doc('scan_directories')
    @scan_ns.expect(scan_directories_model)
    @scan_ns.response(200, 'Scan started successfully', success_model)
    @scan_ns.response(400, 'Invalid request', error_model)
    @scan_ns.response(409, 'Scan already in progress', error_model)
    def post(self):
        """Start a new media scan on specified directories"""
        try:
            data = request.get_json()
            scan_service = current_app.scan_service
            
            result = scan_service.scan_directories(
                directories=data.get('directories', []),
                force_rescan=data.get('force_rescan', False),
                num_workers=data.get('num_workers', 1)
            )
            return result
        except RuntimeError as e:
            return {'error': str(e)}, 409
        except Exception as e:
            return {'error': str(e)}, 400

@scan_ns.route('/status')
class ScanStatus(Resource):
    @scan_ns.doc('get_scan_status')
    @scan_ns.response(200, 'Current scan status', scan_status_model)
    def get(self):
        """Get current scan status"""
        from models import ScanState
        scan_state = ScanState.get_or_create()
        
        if not scan_state:
            return {
                'is_active': False,
                'phase': 'idle',
                'phase_number': 0,
                'total_phases': 3,
                'files_processed': 0,
                'estimated_total': 0,
                'progress_percentage': 0
            }
        
        # Use the utility function to calculate progress
        from utils import ProgressTracker
        progress_tracker = ProgressTracker('scan')
        
        return {
            'is_active': scan_state.is_active,
            'phase': scan_state.phase,
            'phase_number': scan_state.phase_number,
            'total_phases': 3,
            'files_processed': scan_state.files_processed,
            'estimated_total': scan_state.estimated_total,
            'discovery_count': scan_state.discovery_count,
            'phase_current': scan_state.phase_current,
            'phase_total': scan_state.phase_total,
            'current_file': scan_state.current_file,
            'progress_message': scan_state.progress_message,
            'progress_percentage': progress_tracker.calculate_progress_percentage(
                scan_state.phase_number,
                scan_state.phase_current,
                scan_state.phase_total
            )
        }

@scan_ns.route('/cancel')
class CancelScan(Resource):
    @scan_ns.doc('cancel_scan')
    @scan_ns.response(200, 'Scan cancelled', success_model)
    @scan_ns.response(400, 'No active scan', error_model)
    def post(self):
        """Cancel the current scan"""
        try:
            scan_service = current_app.scan_service
            result = scan_service.cancel_scan()
            return result
        except RuntimeError as e:
            return {'error': str(e)}, 400

@scan_ns.route('/parallel-v2')
class ParallelScanV2(Resource):
    @scan_ns.doc('parallel_scan_v2')
    @scan_ns.expect(parallel_scan_model)
    @scan_ns.response(200, 'Parallel scan started', parallel_scan_response_model)
    @scan_ns.response(400, 'Invalid request', error_model)
    @scan_ns.response(503, 'Celery workers not available', error_model)
    def post(self):
        """Start enhanced parallel scan that distributes work across all Celery workers"""
        from pixelprobe.api.scan_routes_parallel import scan_parallel_v2
        from flask import current_app
        with current_app.test_request_context(
            path=request.path,
            method='POST',
            json=request.get_json()
        ):
            return scan_parallel_v2()

@scan_ns.route('/parallel-v2/status/<scan_id>')
class ParallelScanStatus(Resource):
    @scan_ns.doc('get_parallel_scan_status')
    @scan_ns.response(200, 'Scan status retrieved')
    @scan_ns.response(404, 'Scan not found', error_model)
    def get(self, scan_id):
        """Get detailed status of a parallel scan including chunk progress"""
        from pixelprobe.api.scan_routes_parallel import get_parallel_scan_status
        from flask import current_app
        with current_app.test_request_context(
            path=request.path,
            method='GET'
        ):
            return get_parallel_scan_status(scan_id)

@scan_ns.route('/parallel-v2/workers')
class WorkerStatus(Resource):
    @scan_ns.doc('get_worker_status')
    @scan_ns.response(200, 'Worker status retrieved')
    def get(self):
        """Get detailed status of all Celery workers"""
        from pixelprobe.api.scan_routes_parallel import get_worker_status
        from flask import current_app
        with current_app.test_request_context(
            path=request.path,
            method='GET'
        ):
            return get_worker_status()

# Removed /reset-for-rescan endpoint - rarely needed functionality
# Users can achieve the same result by running a scan with force_rescan=true

# Removed duplicate endpoints - use /force-cleanup-scan instead
# The following endpoints were removed as they duplicate functionality:
# - /reset-stuck-scans (duplicate of /force-cleanup-scan)
# - /reset-files-by-path (rarely needed, overly specific)
# - /recover-stuck-scan (duplicate of /force-cleanup-scan)

@scan_ns.route('/force-cleanup-scan')
class ForceCleanupScan(Resource):
    @scan_ns.doc('force_cleanup_scan')
    @scan_ns.response(200, 'Scan forcefully cleaned up')
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Force cleanup of all active scans - emergency recovery"""
        from models import db, ScanState
        from datetime import datetime, timezone
        
        try:
            logger.warning("Force cleanup scan endpoint called - emergency recovery")
            
            # Find all active scans
            active_scans = ScanState.query.filter_by(is_active=True).all()
            cleaned_count = 0
            
            for scan in active_scans:
                logger.warning(f"Force cleaning up scan {scan.scan_id} in phase {scan.phase}")
                scan.is_active = False
                scan.phase = 'crashed'
                scan.error_message = 'Force cleaned up by admin'
                scan.end_time = datetime.now(timezone.utc)
                cleaned_count += 1
            
            # Reset any stuck files
            from models import ScanResult
            stuck_results = ScanResult.query.filter_by(scan_status='scanning').all()
            files_reset = 0
            
            for result in stuck_results:
                result.scan_status = 'pending'
                result.error_message = 'Reset during force cleanup'
                files_reset += 1
            
            db.session.commit()
            
            # Clear scan service state
            if hasattr(current_app, 'scan_service'):
                current_app.scan_service.current_scan_id = None
                current_app.scan_service.is_running = False
            
            logger.warning(f"Force cleanup completed: {cleaned_count} scans stopped, {files_reset} files reset")
            
            return {
                'message': f'Force cleanup completed: {cleaned_count} scans stopped, {files_reset} files reset',
                'scans_cleaned': cleaned_count,
                'files_reset': files_reset
            }
            
        except Exception as e:
            logger.error(f"Error during force cleanup: {e}")
            return {'error': str(e)}, 500

@scan_ns.route('/reset-incomplete-scans')
class ResetIncompleteScans(Resource):
    @scan_ns.doc('reset_incomplete_scans')
    @scan_ns.response(200, 'Incomplete scans reset', reset_incomplete_scans_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Reset files marked as completed but with incomplete scan data
        
        Finds and resets files that show 'N/A' for Tool Details and Scan Date
        due to the v2.2.59 chunk query bug that prevented actual scanning.
        """
        from models import db, ScanResult
        from sqlalchemy import or_
        
        try:
            # Find files marked as completed but missing scan details
            # OR files marked as healthy/not corrupted but never actually scanned
            from sqlalchemy import and_
            incomplete_files = ScanResult.query.filter(
                or_(
                    # Case 1: Marked as completed but no scan data
                    and_(
                        ScanResult.scan_status == 'completed',
                        or_(
                            ScanResult.scan_date.is_(None),
                            ScanResult.scan_output.is_(None),
                            ScanResult.scan_output == '',
                            ScanResult.scan_output == 'N/A'
                        )
                    ),
                    # Case 2: Marked as healthy (is_corrupted=False) but no scan date
                    and_(
                        ScanResult.is_corrupted == False,
                        ScanResult.scan_date.is_(None)
                    ),
                    # Case 3: Any file with scan_date NULL regardless of status
                    # This catches all files that were never actually scanned
                    ScanResult.scan_date.is_(None)
                )
            ).all()
            
            count = len(incomplete_files)
            
            if count == 0:
                return {
                    'message': 'No incomplete scans found',
                    'reset_count': 0
                }
            
            # Reset these files to pending
            for result in incomplete_files:
                result.scan_status = 'pending'
                result.is_corrupted = None  # Reset to unknown
                result.marked_as_good = False
                result.error_message = 'Reset due to incomplete scan data (v2.2.59 fix)'
                result.scan_output = None
                # Keep discovered_date as is
            
            db.session.commit()
            
            logger.info(f"Reset {count} files with incomplete scan data to pending status")
            
            return {
                'message': f'Reset {count} files with incomplete scan data for rescanning',
                'reset_count': count,
                'description': 'These files were marked as completed but had no actual scan results'
            }
            
        except Exception as e:
            logger.error(f"Error resetting incomplete scans: {e}")
            return {'error': str(e)}, 500

@scan_ns.route('/reset-files-by-path')
class ResetFilesByPath(Resource):
    @scan_ns.doc('reset_files_by_path')
    @scan_ns.expect(reset_by_path_model)
    @scan_ns.response(200, 'Files reset', reset_result_model)
    @scan_ns.response(400, 'Invalid request', error_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Reset specific files by their paths"""
        from models import db, ScanResult
        
        data = request.get_json() or {}
        file_path = data.get('file_path')
        file_paths = data.get('file_paths', [])
        
        if file_path:
            file_paths = [file_path]
        
        if not file_paths:
            return {'error': 'No file paths provided'}, 400
        
        try:
            # Reset files by path
            results = ScanResult.query.filter(ScanResult.file_path.in_(file_paths)).all()
            count = len(results)
            
            for result in results:
                result.scan_status = 'pending'
                result.is_corrupted = False
                result.marked_as_good = False
                result.error_message = None
                result.scan_output = None
            
            db.session.commit()
            
            return {
                'message': f'Reset {count} files for rescanning',
                'count': count,
                'type': 'by_path'
            }
            
        except Exception as e:
            logger.error(f"Error resetting files by path: {e}")
            return {'error': str(e)}, 500

# Stats endpoints
@stats_ns.route('/summary')
class StatsSummary(Resource):
    @stats_ns.doc('get_stats_summary')
    @stats_ns.response(200, 'Statistics summary', stats_summary_model)
    def get(self):
        """Get comprehensive statistics summary"""
        stats_service = current_app.stats_service
        return stats_service.get_summary_stats()

@stats_ns.route('/corrupted')
class CorruptedFiles(Resource):
    @stats_ns.doc('get_corrupted_files')
    @stats_ns.param('page', 'Page number', type='integer', default=1)
    @stats_ns.param('per_page', 'Items per page', type='integer', default=20)
    @stats_ns.param('sort_by', 'Sort field', type='string', default='discovered_date')
    @stats_ns.param('sort_order', 'Sort order', type='string', enum=['asc', 'desc'], default='desc')
    def get(self):
        """Get paginated list of corrupted files"""
        stats_service = current_app.stats_service
        
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        sort_by = request.args.get('sort_by', 'discovered_date')
        sort_order = request.args.get('sort_order', 'desc')
        
        return stats_service.get_corrupted_files(
            page=page,
            per_page=per_page,
            sort_by=sort_by,
            sort_order=sort_order
        )

# Maintenance endpoints
@maintenance_ns.route('/file-changes')
class FileChanges(Resource):
    @maintenance_ns.doc('check_file_changes')
    @maintenance_ns.response(200, 'File changes check started', success_model)
    @maintenance_ns.response(409, 'Check already in progress', error_model)
    def post(self):
        """Start checking for file changes"""
        try:
            maintenance_service = current_app.maintenance_service
            result = maintenance_service.start_file_changes_check()
            return result
        except RuntimeError as e:
            return {'error': str(e)}, 409

@maintenance_ns.route('/file-changes-status')
class FileChangesStatus(Resource):
    @maintenance_ns.doc('get_file_changes_status')
    @maintenance_ns.response(200, 'File changes status', file_changes_model)
    def get(self):
        """Get file changes check status"""
        maintenance_service = current_app.maintenance_service
        return maintenance_service.get_file_changes_status()

@maintenance_ns.route('/cleanup-orphaned')
class CleanupOrphaned(Resource):
    @maintenance_ns.doc('cleanup_orphaned')
    @maintenance_ns.response(200, 'Cleanup started', success_model)
    @maintenance_ns.response(409, 'Cleanup already in progress', error_model)
    def post(self):
        """Start cleanup of orphaned database entries"""
        try:
            maintenance_service = current_app.maintenance_service
            result = maintenance_service.start_cleanup()
            return result
        except RuntimeError as e:
            return {'error': str(e)}, 409

@maintenance_ns.route('/cleanup-status')
class CleanupStatus(Resource):
    @maintenance_ns.doc('get_cleanup_status')
    @maintenance_ns.response(200, 'Cleanup status', cleanup_status_model)
    def get(self):
        """Get cleanup operation status"""
        maintenance_service = current_app.maintenance_service
        return maintenance_service.get_cleanup_status()

# Admin endpoints
@admin_ns.route('/configuration')
class Configuration(Resource):
    @admin_ns.doc('get_configuration')
    def get(self):
        """Get all configuration values"""
        from models import Configuration
        configs = Configuration.query.all()
        return [config.to_dict() for config in configs]
    
    @admin_ns.doc('update_configuration')
    @admin_ns.expect(config_model)
    @admin_ns.response(200, 'Configuration updated', success_model)
    def post(self):
        """Update configuration value"""
        from models import Configuration, db
        data = request.get_json()
        
        config = Configuration.query.filter_by(key=data['key']).first()
        if config:
            config.value = data['value']
        else:
            config = Configuration(key=data['key'], value=data['value'])
            db.session.add(config)
        
        db.session.commit()
        return {'message': 'Configuration updated'}

@admin_ns.route('/schedules')
class Schedules(Resource):
    @admin_ns.doc('get_schedules')
    def get(self):
        """Get all scheduled scans"""
        from models import ScanSchedule
        schedules = ScanSchedule.query.all()
        return [schedule.to_dict() for schedule in schedules]
    
    @admin_ns.doc('create_schedule')
    @admin_ns.expect(schedule_model)
    @admin_ns.response(200, 'Schedule created', success_model)
    def post(self):
        """Create a new scheduled scan"""
        from models import ScanSchedule, db
        import json
        
        data = request.get_json()
        
        schedule = ScanSchedule(
            hour=data['hour'],
            minute=data['minute'],
            directories=json.dumps(data['directories']),
            enabled=data.get('enabled', True),
            force_rescan=data.get('force_rescan', False)
        )
        db.session.add(schedule)
        db.session.commit()
        
        # Restart scheduler
        from scheduler import scheduler
        scheduler.restart_scheduler()
        
        return {'message': 'Schedule created', 'id': schedule.id}

# Export endpoints
@export_ns.route('')
class Export(Resource):
    @export_ns.doc('export_scan_results')
    @export_ns.expect(export_request_model, validate=False)
    @export_ns.param('format', 'Export format (csv, json, pdf)', type='string', enum=['csv', 'json', 'pdf'], default='csv')
    @export_ns.param('filter', 'Filter type', type='string', enum=['all', 'corrupted', 'healthy', 'pending', 'error'], default='all')
    @export_ns.param('search', 'Search term to filter file paths', type='string')
    @export_ns.response(200, 'Export successful')
    @export_ns.response(400, 'Invalid request', error_model)
    def get(self):
        """Export scan results with GET parameters"""
        # Import here to avoid circular dependency
        from pixelprobe.api.export_routes import export_scan_results
        from flask import current_app
        with current_app.test_request_context(
            path=request.path,
            query_string=request.query_string,
            method='GET'
        ):
            return export_scan_results()
    
    def post(self):
        """Export scan results with POST body"""
        # Import here to avoid circular dependency
        from pixelprobe.api.export_routes import export_scan_results
        from flask import current_app
        with current_app.test_request_context(
            path=request.path,
            method='POST',
            json=request.get_json()
        ):
            return export_scan_results()

@export_ns.route('/view/<int:result_id>')
class ViewFile(Resource):
    @export_ns.doc('view_file')
    @export_ns.response(200, 'File streamed successfully')
    @export_ns.response(404, 'File not found', error_model)
    def get(self, result_id):
        """View/stream a media file (supports range requests for video streaming)"""
        from pixelprobe.api.export_routes import view_file
        from flask import current_app
        with current_app.test_request_context(
            path=request.path,
            headers=request.headers,
            method='GET'
        ):
            return view_file(result_id)

@export_ns.route('/download/<int:result_id>')
class DownloadFile(Resource):
    @export_ns.doc('download_file')
    @export_ns.response(200, 'File download started')
    @export_ns.response(404, 'File not found', error_model)
    def get(self, result_id):
        """Download a media file"""
        from pixelprobe.api.export_routes import download_file
        from flask import current_app
        with current_app.test_request_context(
            path=request.path,
            method='GET'
        ):
            return download_file(result_id)
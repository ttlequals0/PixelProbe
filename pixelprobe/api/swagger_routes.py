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
    stuck_scan_recovery_model
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

@scan_ns.route('/reset-for-rescan')
class ResetForRescan(Resource):
    @scan_ns.doc('reset_for_rescan')
    @scan_ns.expect(reset_for_rescan_model)
    @scan_ns.response(200, 'Files reset successfully', reset_result_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Reset files for rescanning based on criteria"""
        from models import db, ScanResult
        
        data = request.get_json() or {}
        reset_type = data.get('type', 'all')
        file_ids = data.get('file_ids', [])
        
        try:
            if reset_type == 'selected' and file_ids:
                # Reset specific files
                results = ScanResult.query.filter(ScanResult.id.in_(file_ids)).all()
                count = len(results)
                for result in results:
                    result.scan_status = 'pending'
                    result.is_corrupted = False
                    result.marked_as_good = False
                    result.error_message = None
                    result.scan_output = None
            
            elif reset_type == 'corrupted':
                # Reset all corrupted files
                results = ScanResult.query.filter_by(is_corrupted=True, marked_as_good=False).all()
                count = len(results)
                for result in results:
                    result.scan_status = 'pending'
                    result.is_corrupted = False
                    result.error_message = None
                    result.scan_output = None
            
            elif reset_type == 'error':
                # Reset all files with errors
                results = ScanResult.query.filter_by(scan_status='error').all()
                count = len(results)
                for result in results:
                    result.scan_status = 'pending'
                    result.is_corrupted = False
                    result.error_message = None
                    result.scan_output = None
            
            else:  # all
                # Reset all files
                results = ScanResult.query.all()
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
                'type': reset_type
            }
            
        except Exception as e:
            logger.error(f"Error resetting files for rescan: {e}")
            return {'error': str(e)}, 500

@scan_ns.route('/reset-stuck-scans')
class ResetStuckScans(Resource):
    @scan_ns.doc('reset_stuck_scans')
    @scan_ns.response(200, 'Stuck scans reset', reset_result_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Reset files that are stuck in 'scanning' state"""
        from models import db, ScanResult
        
        try:
            # Find all results stuck in 'scanning' state
            stuck_results = ScanResult.query.filter_by(scan_status='scanning').all()
            count = len(stuck_results)
            
            # Reset them to 'pending'
            for result in stuck_results:
                result.scan_status = 'pending'
                result.error_message = 'Reset from stuck scanning state'
            
            db.session.commit()
            
            return {
                'message': f'Reset {count} stuck files',
                'count': count
            }
        except Exception as e:
            logger.error(f"Error resetting stuck scans: {e}")
            return {'error': str(e)}, 500

@scan_ns.route('/reset-files-by-path')
class ResetFilesByPath(Resource):
    @scan_ns.doc('reset_files_by_path')
    @scan_ns.expect(reset_by_path_model)
    @scan_ns.response(200, 'Files reset successfully', reset_result_model)
    @scan_ns.response(400, 'No file paths provided', error_model)
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
                'count': count
            }
            
        except Exception as e:
            logger.error(f"Error resetting files by path: {e}")
            return {'error': str(e)}, 500

@scan_ns.route('/recover-stuck-scan')
class RecoverStuckScan(Resource):
    @scan_ns.doc('recover_stuck_scan')
    @scan_ns.response(200, 'Scan recovered', stuck_scan_recovery_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Attempt to recover from a stuck scan state"""
        from models import db, ScanState
        
        try:
            # Use scan service to reset stuck scans
            scan_service = current_app.scan_service
            result = scan_service.reset_stuck_scans()
            
            # Reset database scan state
            scan_state = ScanState.get_or_create()
            if scan_state.phase == 'scanning':
                scan_state.error_scan('Scan was stuck and has been recovered')
                db.session.commit()
            
            return {
                'message': 'Scan state recovered successfully',
                'stuck_files_reset': result.get('count', 0)
            }
            
        except Exception as e:
            logger.error(f"Error recovering stuck scan: {e}")
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
@export_ns.route('/csv')
class ExportCSV(Resource):
    @export_ns.doc('export_csv')
    @export_ns.expect(export_request_model)
    def post(self):
        """Export scan results to CSV"""
        export_service = current_app.export_service
        data = request.get_json() or {}
        
        file_ids = data.get('file_ids')
        csv_data = export_service.export_to_csv(file_ids)
        
        from flask import Response
        return Response(
            csv_data,
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=pixelprobe_export.csv'}
        )
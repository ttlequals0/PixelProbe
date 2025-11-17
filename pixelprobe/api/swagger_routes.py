"""
OpenAPI/Swagger route implementations
"""

from flask import request, jsonify, current_app
from flask_restx import Resource
from auth import check_auth
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
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

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
    @scan_ns.response(401, 'Authentication required')
    def get(self):
        """Get current scan status"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

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

@scan_ns.route('/parallel')
class ParallelScan(Resource):
    @scan_ns.doc('parallel_scan')
    @scan_ns.expect(parallel_scan_model)
    @scan_ns.response(200, 'Parallel scan started', parallel_scan_response_model)
    @scan_ns.response(400, 'Invalid request', error_model)
    @scan_ns.response(503, 'Celery workers not available', error_model)
    def post(self):
        """Start enhanced parallel scan that distributes work across all Celery workers"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from pixelprobe.api.scan_routes_parallel import scan_parallel
        from flask import current_app
        with current_app.test_request_context(
            path=request.path,
            method='POST',
            json=request.get_json()
        ):
            return scan_parallel()

@scan_ns.route('/parallel/status/<scan_id>')
class ParallelScanStatus(Resource):
    @scan_ns.doc('get_parallel_scan_status')
    @scan_ns.response(200, 'Scan status retrieved')
    @scan_ns.response(404, 'Scan not found', error_model)
    def get(self, scan_id):
        """Get detailed status of a parallel scan including chunk progress"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from pixelprobe.api.scan_routes_parallel import get_parallel_scan_status
        from flask import current_app
        with current_app.test_request_context(
            path=request.path,
            method='GET'
        ):
            return get_parallel_scan_status(scan_id)

@scan_ns.route('/parallel/workers')
class WorkerStatus(Resource):
    @scan_ns.doc('get_worker_status')
    @scan_ns.response(200, 'Worker status retrieved')
    def get(self):
        """Get detailed status of all Celery workers"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from pixelprobe.api.scan_routes_parallel import get_worker_status
        from flask import current_app
        with current_app.test_request_context(
            path=request.path,
            method='GET'
        ):
            return get_worker_status()

# Removed /reset-for-rescan endpoint - rarely needed functionality
# Users can achieve the same result by running a scan with force_rescan=true

@scan_ns.route('/reset-stuck-scans')
class ResetStuckScans(Resource):
    @scan_ns.doc('reset_stuck_scans')
    @scan_ns.response(200, 'Stuck scans reset', success_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Reset scans that haven't been updated recently (stuck scans)"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from models import db, ScanState
        from datetime import datetime, timezone, timedelta
        import logging
        logger = logging.getLogger(__name__)

        try:
            # Find scans that are active but started more than 30 minutes ago
            # This indicates they're likely stuck
            cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=30)
            stuck_scans = ScanState.query.filter(
                ScanState.is_active == True,
                ScanState.start_time < cutoff_time
            ).all()

            reset_count = 0
            for scan in stuck_scans:
                logger.warning(f"Resetting stuck scan {scan.scan_id}, started at: {scan.start_time}")
                scan.is_active = False
                scan.phase = 'crashed'
                scan.progress_message = 'Scan stuck - automatically reset'
                reset_count += 1

            db.session.commit()

            return {
                'message': f'Reset {reset_count} stuck scans',
                'scans_reset': reset_count
            }
        except Exception as e:
            logger.error(f"Error resetting stuck scans: {str(e)}")
            return {'error': str(e)}, 500

@scan_ns.route('/force-cleanup-scan')
class ForceCleanupScan(Resource):
    @scan_ns.doc('force_cleanup_scan')
    @scan_ns.response(200, 'Scan forcefully cleaned up')
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Force cleanup of all active scans - emergency recovery"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

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

@scan_ns.route('/scan', methods=['POST'])
class ScanMain(Resource):
    @scan_ns.doc('start_scan')
    @scan_ns.response(200, 'Scan started successfully')
    @scan_ns.response(409, 'Scan already in progress', error_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Start scanning all media files in configured directories"""
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from pixelprobe.api.scan_routes import scan_bp
        from flask import current_app
        with current_app.test_request_context(
            path='/api/scan',
            headers=request.headers,
            method='POST',
            json=request.get_json()
        ):
            from pixelprobe.api.scan_routes import scan
            return scan()

@scan_ns.route('/scan-status')
class GetScanStatus(Resource):
    @scan_ns.doc('get_scan_status')
    @scan_ns.response(200, 'Scan status retrieved', scan_status_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def get(self):
        """Get current scan status and progress"""
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from models import ScanState
        try:
            scan_state = ScanState.query.order_by(ScanState.id.desc()).first()

            if not scan_state:
                return {
                    'is_active': False,
                    'phase': 'idle',
                    'message': 'No scans have been run yet'
                }

            return {
                'scan_id': scan_state.scan_id,
                'is_active': scan_state.is_active,
                'phase': scan_state.phase,
                'phase_number': scan_state.phase_number,
                'phase_current': scan_state.phase_current,
                'phase_total': scan_state.phase_total,
                'files_processed': scan_state.files_processed,
                'estimated_total': scan_state.estimated_total,
                'discovery_count': scan_state.discovery_count,
                'start_time': scan_state.start_time.isoformat() if scan_state.start_time else None,
                'end_time': scan_state.end_time.isoformat() if scan_state.end_time else None,
                'current_file': scan_state.current_file,
                'progress_message': scan_state.progress_message,
                'error_message': scan_state.error_message
            }
        except Exception as e:
            logger.error(f"Error getting scan status: {e}")
            return {'error': str(e)}, 500

@scan_ns.route('/cancel-scan')
class CancelCurrentScan(Resource):
    @scan_ns.doc('cancel_current_scan')
    @scan_ns.response(200, 'Scan cancelled successfully')
    @scan_ns.response(400, 'No active scan to cancel', error_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Cancel the currently running scan"""
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from models import db, ScanState
        import logging
        logger = logging.getLogger(__name__)

        try:
            scan_state = ScanState.query.filter_by(is_active=True).first()

            if not scan_state:
                return {'error': 'No active scan to cancel'}, 400

            logger.info(f"Cancelling scan {scan_state.scan_id}")
            scan_state.is_active = False
            scan_state.phase = 'cancelled'
            scan_state.progress_message = 'Scan cancelled by user'
            db.session.commit()

            # Signal the scan service to stop
            if hasattr(current_app, 'scan_service'):
                current_app.scan_service.is_running = False

            return {'message': f'Scan {scan_state.scan_id} cancelled successfully'}

        except Exception as e:
            logger.error(f"Error cancelling scan: {e}")
            return {'error': str(e)}, 500

@scan_ns.route('/scan-results')
class GetScanResults(Resource):
    @scan_ns.doc('get_scan_results')
    @scan_ns.response(200, 'Scan results retrieved')
    @scan_ns.response(500, 'Internal error', error_model)
    def get(self):
        """Get all scan results with optional filtering"""
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from models import ScanResult
        from pixelprobe.utils.timezone_utils import from_utc_to_configured
        import os

        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 100, type=int)
            scan_status = request.args.get('scan_status', 'all')
            is_corrupted = request.args.get('is_corrupted', 'all')
            has_warnings = request.args.get('has_warnings', 'all')
            search_query = request.args.get('search', '').strip()
            sort_field = request.args.get('sort_field', 'scan_date')
            sort_order = request.args.get('sort_order', 'desc')

            # Build query
            query = ScanResult.query

            # Apply search filter
            if search_query:
                query = query.filter(ScanResult.file_path.ilike(f'%{search_query}%'))

            # Apply status filter
            if scan_status != 'all':
                query = query.filter_by(scan_status=scan_status)

            # Apply corruption filter
            if is_corrupted == 'true':
                query = query.filter_by(is_corrupted=True).filter_by(marked_as_good=False)
            elif is_corrupted == 'false':
                query = query.filter(
                    (ScanResult.is_corrupted == False) |
                    (ScanResult.marked_as_good == True)
                )

            # Apply warnings filter
            if has_warnings == 'true':
                query = query.filter(
                    (ScanResult.has_warnings == True) &
                    (ScanResult.marked_as_good == False) &
                    (ScanResult.is_corrupted == False)
                )

            # Apply sorting
            field_mapping = {
                'scan_date': ScanResult.scan_date,
                'file_path': ScanResult.file_path,
                'file_size': ScanResult.file_size,
                'file_type': ScanResult.file_type,
                'scan_status': ScanResult.scan_status,
                'status': ScanResult.is_corrupted,
                'is_corrupted': ScanResult.is_corrupted,
                'marked_as_good': ScanResult.marked_as_good,
                'scan_tool': ScanResult.scan_tool,
                'corruption_details': ScanResult.corruption_details,
                'discovered_date': ScanResult.discovered_date,
                'last_modified': ScanResult.last_modified
            }

            if sort_field in field_mapping:
                field_attr = field_mapping[sort_field]
                if sort_order.lower() == 'asc':
                    query = query.order_by(field_attr.asc())
                else:
                    query = query.order_by(field_attr.desc())
            else:
                query = query.order_by(ScanResult.scan_date.desc())

            # Paginate - handle -1 as "show all"
            if per_page == -1:
                all_results = query.all()
                class MockPagination:
                    def __init__(self, items, total):
                        self.items = items
                        self.total = total
                        self.pages = 1
                pagination = MockPagination(all_results, len(all_results))
            else:
                pagination = query.paginate(page=page, per_page=per_page, error_out=False)

            # Build response
            results = []
            for result in pagination.items:
                result_dict = result.to_dict()

                # Convert timestamps to configured timezone
                if result.scan_date:
                    display_dt = from_utc_to_configured(result.scan_date)
                    result_dict['scan_date'] = display_dt.isoformat() if display_dt else None

                if result.discovered_date:
                    display_dt = from_utc_to_configured(result.discovered_date)
                    result_dict['discovered_date'] = display_dt.isoformat() if display_dt else None

                if result.creation_date:
                    display_dt = from_utc_to_configured(result.creation_date)
                    result_dict['creation_date'] = display_dt.isoformat() if display_dt else None

                if result.last_modified:
                    display_dt = from_utc_to_configured(result.last_modified)
                    result_dict['last_modified'] = display_dt.isoformat() if display_dt else None

                # Add file_name for frontend convenience
                result_dict['file_name'] = os.path.basename(result.file_path) if result.file_path else ''

                results.append(result_dict)

            return {
                'results': results,
                'total': pagination.total,
                'page': page,
                'per_page': per_page,
                'pages': pagination.pages
            }

        except Exception as e:
            logger.error(f"Error retrieving scan results: {e}")
            return {'error': str(e)}, 500

@scan_ns.route('/scan-results/<int:result_id>')
class GetScanResult(Resource):
    @scan_ns.doc('get_scan_result')
    @scan_ns.response(200, 'Scan result retrieved')
    @scan_ns.response(404, 'Result not found', error_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def get(self, result_id):
        """Get a specific scan result by ID"""
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from models import ScanResult
        try:
            result = ScanResult.query.get(result_id)
            if not result:
                return {'error': 'Result not found'}, 404

            return {
                'id': result.id,
                'file_path': result.file_path,
                'file_name': result.file_name,
                'file_size': result.file_size,
                'scan_status': result.scan_status,
                'is_corrupted': result.is_corrupted,
                'marked_as_good': result.marked_as_good,
                'scan_date': result.scan_date.isoformat() if result.scan_date else None,
                'discovered_date': result.discovered_date.isoformat() if result.discovered_date else None,
                'tool_output': result.tool_output,
                'scan_output': result.scan_output,
                'error_message': result.error_message
            }
        except Exception as e:
            logger.error(f"Error getting scan result: {e}")
            return {'error': str(e)}, 500

@scan_ns.route('/error-files')
class GetErrorFiles(Resource):
    @scan_ns.doc('get_error_files',
                 params={
                     'page': {'description': 'Page number', 'type': 'int', 'default': 1},
                     'per_page': {'description': 'Results per page (use -1 for all)', 'type': 'int', 'default': 100},
                     'sort_field': {'description': 'Field to sort by', 'type': 'string', 'default': 'scan_date', 'enum': ['scan_date', 'file_path', 'file_size', 'file_type', 'scan_duration']},
                     'sort_order': {'description': 'Sort order', 'type': 'string', 'default': 'desc', 'enum': ['asc', 'desc']},
                     'search': {'description': 'Filter by file path (optional)', 'type': 'string'}
                 })
    @scan_ns.response(200, 'Error files retrieved')
    @scan_ns.response(401, 'Authentication required')
    @scan_ns.response(500, 'Internal error', error_model)
    def get(self):
        """Get list of files that failed to scan

        Returns paginated list of files with scan_status='error' including error messages,
        scan dates, duration, and tool information. Supports filtering by file path and
        sorting by various fields.
        """
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from models import ScanResult
        from pixelprobe.utils.timezone_utils import from_utc_to_configured

        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 100, type=int)
            sort_field = request.args.get('sort_field', 'scan_date')
            sort_order = request.args.get('sort_order', 'desc')
            search_query = request.args.get('search', '').strip()

            # Build query for error files
            query = ScanResult.query.filter_by(scan_status='error')

            # Apply search filter if provided
            if search_query:
                query = query.filter(ScanResult.file_path.ilike(f'%{search_query}%'))

            # Apply sorting
            field_mapping = {
                'scan_date': ScanResult.scan_date,
                'file_path': ScanResult.file_path,
                'file_size': ScanResult.file_size,
                'file_type': ScanResult.file_type,
                'scan_duration': ScanResult.scan_duration
            }

            if sort_field in field_mapping:
                field_attr = field_mapping[sort_field]
                if sort_order.lower() == 'asc':
                    query = query.order_by(field_attr.asc())
                else:
                    query = query.order_by(field_attr.desc())
            else:
                # Default sorting by most recent errors first
                query = query.order_by(ScanResult.scan_date.desc())

            # Paginate - handle -1 as "show all"
            if per_page == -1:
                all_results = query.all()
                class MockPagination:
                    def __init__(self, items, total):
                        self.items = items
                        self.total = total
                        self.pages = 1
                pagination = MockPagination(all_results, len(all_results))
            else:
                pagination = query.paginate(page=page, per_page=per_page, error_out=False)

            # Build response
            results = []
            for result in pagination.items:
                error_file = {
                    'id': result.id,
                    'file_path': result.file_path,
                    'file_size': result.file_size,
                    'file_type': result.file_type,
                    'error_message': result.error_message,
                    'scan_date': None,
                    'scan_duration': result.scan_duration,
                    'scan_tool': result.scan_tool,
                    'discovered_date': None
                }

                # Convert timestamps to configured timezone
                if result.scan_date:
                    display_dt = from_utc_to_configured(result.scan_date)
                    error_file['scan_date'] = display_dt.isoformat() if display_dt else None

                if result.discovered_date:
                    display_dt = from_utc_to_configured(result.discovered_date)
                    error_file['discovered_date'] = display_dt.isoformat() if display_dt else None

                results.append(error_file)

            return {
                'error_files': results,
                'total': pagination.total,
                'pages': pagination.pages,
                'current_page': page,
                'per_page': per_page if per_page != -1 else pagination.total
            }

        except Exception as e:
            logger.error(f"Error retrieving error files: {e}")
            return {'error': str(e)}, 500

@scan_ns.route('/scan-file')
class ScanSingleFile(Resource):
    @scan_ns.doc('scan_file')
    @scan_ns.response(200, 'File scan initiated')
    @scan_ns.response(400, 'Invalid request', error_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Scan a single file by ID"""
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from pixelprobe.api.scan_routes import scan_file
        from flask import current_app
        with current_app.test_request_context(
            path='/api/scan-file',
            headers=request.headers,
            method='POST',
            json=request.get_json()
        ):
            return scan_file()

@scan_ns.route('/mark-as-good')
class MarkAsGood(Resource):
    @scan_ns.doc('mark_as_good')
    @scan_ns.response(200, 'File marked as good')
    @scan_ns.response(400, 'Invalid request', error_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Mark a file as good (not corrupted)"""
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from pixelprobe.api.admin_routes import mark_as_good
        from flask import current_app
        with current_app.test_request_context(
            path='/api/mark-as-good',
            headers=request.headers,
            method='POST',
            json=request.get_json()
        ):
            return mark_as_good()

@scan_ns.route('/reset-for-rescan')
class ResetForRescan(Resource):
    @scan_ns.doc('reset_for_rescan')
    @scan_ns.response(200, 'Files reset for rescan', reset_for_rescan_model)
    @scan_ns.response(400, 'Invalid request', error_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Reset files to pending status for rescanning"""
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from pixelprobe.api.scan_routes import reset_for_rescan
        from flask import current_app
        with current_app.test_request_context(
            path='/api/reset-for-rescan',
            headers=request.headers,
            method='POST',
            json=request.get_json()
        ):
            return reset_for_rescan()

@scan_ns.route('/reset-incomplete-scans')
class ResetIncompleteScans(Resource):
    @scan_ns.doc('reset_incomplete_scans')
    @scan_ns.response(200, 'Incomplete scans reset', reset_incomplete_scans_model)
    @scan_ns.response(500, 'Internal error', error_model)
    def post(self):
        """Reset files marked as completed but with incomplete scan data

        Finds and resets files that show 'N/A' for Tool Details and Scan Date.
        """
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

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
                result.error_message = 'Reset due to incomplete scan data'
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

@scan_ns.route('/diagnose-incomplete-scans')
class DiagnoseIncompleteScans(Resource):
    @scan_ns.doc('diagnose_incomplete_scans')
    @scan_ns.response(200, 'Diagnosis results')
    @scan_ns.response(500, 'Internal error', error_model)
    def get(self):
        """Diagnose files with incomplete scan data without resetting them"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from models import db, ScanResult
        from sqlalchemy import or_
        import logging
        logger = logging.getLogger(__name__)

        try:
            incomplete_files = ScanResult.query.filter(
                ScanResult.scan_status == 'completed',
                or_(
                    ScanResult.scan_date == None,
                    ScanResult.scan_output == None
                )
            ).all()

            return {
                'incomplete_count': len(incomplete_files),
                'sample_files': [f.file_path for f in incomplete_files[:10]],
                'message': f'Found {len(incomplete_files)} files with incomplete scan data'
            }
        except Exception as e:
            logger.error(f"Error diagnosing incomplete scans: {e}")
            return {'error': str(e)}, 500

@scan_ns.route('/worker-status')
class WorkerStatus(Resource):
    @scan_ns.doc('get_worker_status_global')
    @scan_ns.response(200, 'Worker status retrieved')
    @scan_ns.response(500, 'Internal error', error_model)
    def get(self):
        """Get Celery worker status and queue information"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        try:
            from celery import current_app as celery_app

            # Get worker stats
            stats = celery_app.control.inspect().stats()
            active = celery_app.control.inspect().active()
            reserved = celery_app.control.inspect().reserved()

            worker_count = len(stats) if stats else 0
            active_tasks = sum(len(tasks) for tasks in (active or {}).values())
            reserved_tasks = sum(len(tasks) for tasks in (reserved or {}).values())

            return {
                'workers': worker_count,
                'active_tasks': active_tasks,
                'reserved_tasks': reserved_tasks,
                'worker_details': stats or {}
            }
        except Exception as e:
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
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

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
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        stats_service = current_app.stats_service
        return stats_service.get_system_info()

@stats_ns.route('/corrupted')
class CorruptedFiles(Resource):
    @stats_ns.doc('get_corrupted_files')
    @stats_ns.param('page', 'Page number', type='integer', default=1)
    @stats_ns.param('per_page', 'Items per page', type='integer', default=20)
    @stats_ns.param('sort_by', 'Sort field', type='string', default='discovered_date')
    @stats_ns.param('sort_order', 'Sort order', type='string', enum=['asc', 'desc'], default='desc')
    def get(self):
        """Get paginated list of corrupted files"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

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

@stats_ns.route('/stats')
class GetStats(Resource):
    @stats_ns.doc('get_stats')
    @stats_ns.response(200, 'Statistics retrieved')
    def get(self):
        """Get system statistics"""
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from pixelprobe.api.stats_routes import get_stats
        from flask import current_app
        with current_app.test_request_context(
            path='/api/stats',
            headers=request.headers,
            method='GET'
        ):
            return get_stats()

@stats_ns.route('/system-info')
class GetSystemInfo(Resource):
    @stats_ns.doc('get_system_info')
    @stats_ns.response(200, 'System info retrieved')
    def get(self):
        """Get system information"""
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        try:
            stats_service = current_app.stats_service
            return stats_service.get_system_info()
        except Exception as e:
            logger.error(f"Error getting system info: {e}")
            return {'error': str(e)}, 500

# Maintenance endpoints
@maintenance_ns.route('/file-changes')
class FileChanges(Resource):
    @maintenance_ns.doc('check_file_changes')
    @maintenance_ns.response(200, 'File changes check started', success_model)
    @maintenance_ns.response(409, 'Check already in progress', error_model)
    def post(self):
        """Start checking for file changes"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

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
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        maintenance_service = current_app.maintenance_service
        return maintenance_service.get_file_changes_status()

@maintenance_ns.route('/cancel-file-changes')
class CancelFileChanges(Resource):
    @maintenance_ns.doc('cancel_file_changes')
    @maintenance_ns.response(200, 'File changes check cancelled', success_model)
    @maintenance_ns.response(400, 'No active file changes check', error_model)
    @maintenance_ns.response(401, 'Authentication required', error_model)
    def post(self):
        """Cancel the current file changes check operation"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        try:
            from models import FileChangesState, db
            from pixelprobe.api.maintenance_routes import file_changes_state_lock, file_changes_state
            import logging
            logger = logging.getLogger(__name__)

            file_changes_record = FileChangesState.query.order_by(FileChangesState.id.desc()).first()

            if file_changes_record and file_changes_record.is_active:
                file_changes_record.cancel_requested = True
                file_changes_record.progress_message = 'Cancellation requested...'
                db.session.commit()

                with file_changes_state_lock:
                    file_changes_state['cancel_requested'] = True

                logger.info("File changes check cancellation requested")
                return {'message': 'File changes check cancellation requested'}
            else:
                return {'error': 'No active file changes check to cancel'}, 400
        except Exception as e:
            return {'error': str(e)}, 500

@maintenance_ns.route('/reset-file-changes-state')
class ResetFileChangesState(Resource):
    @maintenance_ns.doc('reset_file_changes_state')
    @maintenance_ns.response(200, 'File changes state reset', success_model)
    @maintenance_ns.response(401, 'Authentication required', error_model)
    def post(self):
        """Force reset file changes state in case of stuck operation"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        # Call the actual route function
        from pixelprobe.api.maintenance_routes import reset_file_changes_state
        return reset_file_changes_state()

@maintenance_ns.route('/cleanup-orphaned')
class CleanupOrphaned(Resource):
    @maintenance_ns.doc('cleanup_orphaned')
    @maintenance_ns.response(200, 'Cleanup started', success_model)
    @maintenance_ns.response(409, 'Cleanup already in progress', error_model)
    def post(self):
        """Start cleanup of orphaned database entries"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

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
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        maintenance_service = current_app.maintenance_service
        return maintenance_service.get_cleanup_status()

@maintenance_ns.route('/cancel-cleanup')
class CancelCleanup(Resource):
    @maintenance_ns.doc('cancel_cleanup')
    @maintenance_ns.response(200, 'Cleanup cancelled', success_model)
    @maintenance_ns.response(400, 'No active cleanup', error_model)
    @maintenance_ns.response(401, 'Authentication required', error_model)
    def post(self):
        """Cancel the current cleanup operation"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        # Call the actual route function
        from pixelprobe.api.maintenance_routes import cancel_cleanup
        return cancel_cleanup()

@maintenance_ns.route('/reset-cleanup-state')
class ResetCleanupState(Resource):
    @maintenance_ns.doc('reset_cleanup_state')
    @maintenance_ns.response(200, 'Cleanup state reset', success_model)
    @maintenance_ns.response(401, 'Authentication required', error_model)
    def post(self):
        """Force reset cleanup state in case of stuck operation"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        # Call the actual route function
        from pixelprobe.api.maintenance_routes import reset_cleanup_state
        return reset_cleanup_state()

@maintenance_ns.route('/vacuum')
class Vacuum(Resource):
    @maintenance_ns.doc('vacuum_database')
    @maintenance_ns.response(200, 'Vacuum completed', success_model)
    @maintenance_ns.response(401, 'Authentication required', error_model)
    def post(self):
        """Run VACUUM on the PostgreSQL database to reclaim space"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        # Call the actual route function
        from pixelprobe.api.maintenance_routes import vacuum_database
        return vacuum_database()

@maintenance_ns.route('/test-cleanup')
class TestCleanup(Resource):
    @maintenance_ns.doc('test_cleanup')
    @maintenance_ns.response(200, 'Test cleanup results')
    @maintenance_ns.response(401, 'Authentication required', error_model)
    def get(self):
        """Test cleanup to see what would be cleaned without actually doing it"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        # Call the actual route function
        from pixelprobe.api.maintenance_routes import test_cleanup
        return test_cleanup()

# Admin endpoints
@admin_ns.route('/configuration')
class Configuration(Resource):
    @admin_ns.doc('get_configuration')
    def get(self):
        """Get all configuration values"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from models import Configuration
        configs = Configuration.query.all()
        return [config.to_dict() for config in configs]
    
    @admin_ns.doc('update_configuration')
    @admin_ns.expect(config_model)
    @admin_ns.response(200, 'Configuration updated', success_model)
    def post(self):
        """Update configuration value"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

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
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from models import ScanSchedule
        schedules = ScanSchedule.query.all()
        return [schedule.to_dict() for schedule in schedules]
    
    @admin_ns.doc('create_schedule')
    @admin_ns.expect(schedule_model)
    @admin_ns.response(200, 'Schedule created', success_model)
    def post(self):
        """Create a new scheduled scan"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

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
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        try:
            from pixelprobe.services.export_service import ExportService
            export_service = ExportService()

            # Get parameters
            export_format = request.args.get('format', 'csv').lower()
            filter_type = request.args.get('filter', 'all')
            search_query = request.args.get('search', '')

            # Use the export service directly
            return export_service.export_results(
                export_format=export_format,
                filter_type=filter_type,
                search_query=search_query
            )
        except Exception as e:
            logger.error(f"Error exporting results: {e}")
            return {'error': str(e)}, 500

    def post(self):
        """Export scan results with POST body"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        try:
            from pixelprobe.services.export_service import ExportService
            export_service = ExportService()

            data = request.get_json() or {}
            filters = data.get('filters', {})

            export_format = filters.get('format', 'csv').lower()
            filter_type = filters.get('filter', 'all')
            search_query = filters.get('search', '')

            return export_service.export_results(
                export_format=export_format,
                filter_type=filter_type,
                search_query=search_query
            )
        except Exception as e:
            logger.error(f"Error exporting results: {e}")
            return {'error': str(e)}, 500

@export_ns.route('/view/<int:result_id>')
class ViewFile(Resource):
    @export_ns.doc('view_file')
    @export_ns.response(200, 'File streamed successfully')
    @export_ns.response(404, 'File not found', error_model)
    def get(self, result_id):
        """View/stream a media file (supports range requests for video streaming)"""
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

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
        # Check authentication
        if not check_auth():
            return {'error': 'Authentication required'}, 401

        from pixelprobe.api.export_routes import download_file
        from flask import current_app
        with current_app.test_request_context(
            path=request.path,
            method='GET'
        ):
            return download_file(result_id)
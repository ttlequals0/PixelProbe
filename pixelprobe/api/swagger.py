"""
OpenAPI/Swagger configuration for PixelProbe API
"""

from flask import Blueprint
from flask_restx import Api, Resource, fields, Namespace
from version import __version__

# Create API blueprint
api_bp = Blueprint('api_swagger', __name__, url_prefix='/api/v1')

# Initialize API with Swagger documentation
api = Api(
    api_bp,
    version=__version__,
    title='PixelProbe API',
    description='REST API for PixelProbe media file corruption detection system',
    doc='/docs',  # Swagger UI will be available at /api/v1/docs
    ordered=True,
    validate=True
)

# Define namespaces
scan_ns = Namespace('scan', description='Media scanning operations')
stats_ns = Namespace('stats', description='Statistics and reporting')
maintenance_ns = Namespace('maintenance', description='Maintenance operations')
admin_ns = Namespace('admin', description='Administrative functions')
export_ns = Namespace('export', description='Data export operations')

# Add namespaces to API
api.add_namespace(scan_ns)
api.add_namespace(stats_ns)
api.add_namespace(maintenance_ns)
api.add_namespace(admin_ns)
api.add_namespace(export_ns)

# Define common models
error_model = api.model('Error', {
    'error': fields.String(description='Error message'),
    'status': fields.Integer(description='HTTP status code')
})

success_model = api.model('Success', {
    'message': fields.String(description='Success message'),
    'status': fields.String(description='Operation status')
})

# Scan models
scan_directories_model = api.model('ScanDirectories', {
    'directories': fields.List(fields.String, required=True, description='List of directories to scan'),
    'force_rescan': fields.Boolean(default=False, description='Force rescan of already scanned files'),
    'num_workers': fields.Integer(default=1, min=1, max=16, description='Number of parallel workers')
})

scan_status_model = api.model('ScanStatus', {
    'is_active': fields.Boolean(description='Whether scan is currently active'),
    'phase': fields.String(description='Current scan phase'),
    'phase_number': fields.Integer(description='Current phase number'),
    'total_phases': fields.Integer(description='Total number of phases'),
    'files_processed': fields.Integer(description='Number of files processed'),
    'estimated_total': fields.Integer(description='Estimated total files'),
    'progress_percentage': fields.Float(description='Progress percentage'),
    'current_file': fields.String(description='Currently processing file'),
    'progress_message': fields.String(description='Progress message with ETA')
})

# Stats models
stats_summary_model = api.model('StatsSummary', {
    'overview': fields.Raw(description='Overview statistics'),
    'recent_corrupted': fields.List(fields.Raw, description='Recently found corrupted files'),
    'storage': fields.Raw(description='Storage statistics'),
    'performance': fields.Raw(description='Performance metrics'),
    'current_time': fields.DateTime(description='Current server time'),
    'timezone': fields.String(description='Server timezone'),
    'version': fields.String(description='Application version')
})

# Maintenance models
file_changes_model = api.model('FileChangesStatus', {
    'is_running': fields.Boolean(description='Whether check is running'),
    'phase': fields.String(description='Current phase'),
    'files_processed': fields.Integer(description='Files processed'),
    'total_files': fields.Integer(description='Total files to check'),
    'changes_found': fields.Integer(description='Number of changed files found'),
    'corrupted_found': fields.Integer(description='Number of newly corrupted files'),
    'progress_percentage': fields.Float(description='Progress percentage'),
    'progress_message': fields.String(description='Progress message with ETA')
})

cleanup_status_model = api.model('CleanupStatus', {
    'is_running': fields.Boolean(description='Whether cleanup is running'),
    'phase': fields.String(description='Current phase'),
    'files_processed': fields.Integer(description='Files processed'),
    'total_files': fields.Integer(description='Total files to check'),
    'orphaned_found': fields.Integer(description='Number of orphaned entries found'),
    'progress_percentage': fields.Float(description='Progress percentage'),
    'progress_message': fields.String(description='Progress message with ETA')
})

# Export models
export_request_model = api.model('ExportRequest', {
    'file_ids': fields.List(fields.Integer, description='Optional list of file IDs to export')
})

# Configuration models
config_model = api.model('Configuration', {
    'key': fields.String(required=True, description='Configuration key'),
    'value': fields.String(required=True, description='Configuration value')
})

schedule_model = api.model('Schedule', {
    'hour': fields.Integer(required=True, min=0, max=23, description='Hour to run (0-23)'),
    'minute': fields.Integer(required=True, min=0, max=59, description='Minute to run (0-59)'),
    'directories': fields.List(fields.String, required=True, description='Directories to scan'),
    'enabled': fields.Boolean(default=True, description='Whether schedule is enabled'),
    'force_rescan': fields.Boolean(default=False, description='Force rescan of files')
})

# Route implementations will be imported in app.py after blueprint registration
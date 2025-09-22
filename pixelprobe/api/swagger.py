"""
OpenAPI/Swagger configuration for PixelProbe API
"""

from flask import Blueprint
from flask_restx import Api, Resource, fields, Namespace
from version import __version__

# Create API blueprint
api_bp = Blueprint('api_swagger', __name__, url_prefix='/api/v1')

# Custom Swagger UI configuration with dark mode
SWAGGER_UI_DOC_EXPANSION = 'list'
SWAGGER_UI_OPERATION_ID = True
SWAGGER_UI_REQUEST_DURATION = True

# Authorization definitions
authorizations = {
    'apikey': {
        'type': 'apiKey',
        'in': 'header',
        'name': 'Authorization',
        'description': 'Bearer token authentication. Use format: Bearer <token>'
    },
    'session': {
        'type': 'apiKey',
        'in': 'cookie',
        'name': 'session',
        'description': 'Session-based authentication via cookie'
    }
}

# Initialize API with Swagger documentation
api = Api(
    api_bp,
    version=__version__,
    title='PixelProbe API',
    description='REST API for PixelProbe media file corruption detection system',
    doc='/docs',  # Swagger UI will be available at /api/v1/docs
    ordered=True,
    validate=True,
    authorizations=authorizations,
    security='apikey'  # Default security for all endpoints
)

# Define namespaces
auth_ns = Namespace('auth', description='Authentication and user management')
scan_ns = Namespace('scan', description='Media scanning operations')
stats_ns = Namespace('stats', description='Statistics and reporting')
maintenance_ns = Namespace('maintenance', description='Maintenance operations')
admin_ns = Namespace('admin', description='Administrative functions')
export_ns = Namespace('export', description='Data export operations')

# Add namespaces to API
api.add_namespace(auth_ns)
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

# Authentication models
login_model = api.model('Login', {
    'username': fields.String(required=True, description='Username'),
    'password': fields.String(required=True, description='Password'),
    'remember': fields.Boolean(default=False, description='Remember login session')
})

login_response_model = api.model('LoginResponse', {
    'success': fields.Boolean(description='Login success status'),
    'user': fields.Raw(description='User information')
})

user_model = api.model('User', {
    'id': fields.Integer(description='User ID'),
    'username': fields.String(description='Username'),
    'email': fields.String(description='Email address'),
    'is_admin': fields.Boolean(description='Admin status'),
    'created_at': fields.DateTime(description='Creation timestamp'),
    'last_login': fields.DateTime(description='Last login timestamp')
})

create_user_model = api.model('CreateUser', {
    'username': fields.String(required=True, description='Username'),
    'email': fields.String(required=True, description='Email address'),
    'password': fields.String(required=True, description='Password (min 8 characters)'),
    'is_admin': fields.Boolean(default=True, description='Grant admin privileges')
})

api_token_model = api.model('APIToken', {
    'id': fields.Integer(description='Token ID'),
    'description': fields.String(description='Token description'),
    'created_at': fields.DateTime(description='Creation timestamp'),
    'last_used': fields.DateTime(description='Last usage timestamp'),
    'expires_at': fields.DateTime(description='Expiration timestamp')
})

create_token_model = api.model('CreateToken', {
    'description': fields.String(required=True, description='Token description'),
    'expires_in_days': fields.Integer(description='Days until expiration (optional)')
})

token_response_model = api.model('TokenResponse', {
    'token': fields.String(description='API token (only shown once)'),
    'token_info': fields.Raw(description='Token information')
})

password_change_model = api.model('PasswordChange', {
    'current_password': fields.String(required=True, description='Current password'),
    'new_password': fields.String(required=True, description='New password (min 8 characters)')
})

auth_status_model = api.model('AuthStatus', {
    'authenticated': fields.Boolean(description='Authentication status'),
    'first_run': fields.Boolean(description='First run status'),
    'user': fields.Raw(description='User information if authenticated')
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

parallel_scan_model = api.model('ParallelScan', {
    'directories': fields.List(fields.String, required=True, description='List of directories to scan'),
    'force_rescan': fields.Boolean(default=False, description='Force rescan of already scanned files'),
})

parallel_scan_response_model = api.model('ParallelScanResponse', {
    'status': fields.String(description='Scan launch status'),
    'scan_id': fields.String(description='Unique scan identifier'),
    'task_id': fields.String(description='Celery task ID'),
    'message': fields.String(description='Status message'),
    'celery_workers': fields.Integer(description='Number of available Celery workers'),
    'scan_type': fields.String(description='Type of scan'),
    'directories': fields.List(fields.String, description='Directories being scanned')
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
cleanup_model = api.model('Cleanup', {
    'deleted_files': fields.Integer(description='Number of database entries removed'),
    'orphaned_records': fields.Integer(description='Number of orphaned records found'),
    'message': fields.String(description='Cleanup status message')
})

cleanup_status_model = api.model('CleanupStatus', {
    'deleted_files': fields.Integer(description='Number of database entries removed'),
    'orphaned_records': fields.Integer(description='Number of orphaned records found'),
    'message': fields.String(description='Cleanup status message')
})

file_changes_model = api.model('FileChanges', {
    'added': fields.Integer(description='Number of files added'),
    'modified': fields.Integer(description='Number of files modified'),
    'deleted': fields.Integer(description='Number of files deleted'),
    'total_changes': fields.Integer(description='Total number of changes'),
    'message': fields.String(description='Status message')
})

export_format_model = api.model('ExportFormat', {
    'format': fields.String(
        required=True,
        enum=['csv', 'json', 'excel'],
        description='Export format'
    ),
    'include_all': fields.Boolean(default=False, description='Include all results, not just corrupted files')
})

export_request_model = api.model('ExportRequest', {
    'format': fields.String(
        required=True,
        enum=['csv', 'json', 'excel'],
        description='Export format'
    ),
    'include_all': fields.Boolean(default=False, description='Include all results, not just corrupted files')
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

# Reset models
reset_for_rescan_model = api.model('ResetForRescan', {
    'type': fields.String(
        required=True,
        enum=['all', 'selected', 'corrupted', 'error'],
        description='Type of reset: all (reset all files), selected (reset specific files), corrupted (reset corrupted files), error (reset error files)'
    ),
    'file_ids': fields.List(
        fields.Integer,
        description='List of file IDs to reset (required only for type=selected)'
    )
})

reset_result_model = api.model('ResetResult', {
    'message': fields.String(description='Result message'),
    'count': fields.Integer(description='Number of files reset'),
    'type': fields.String(description='Type of reset performed')
})

reset_by_path_model = api.model('ResetByPath', {
    'file_path': fields.String(description='Single file path to reset'),
    'file_paths': fields.List(fields.String, description='List of file paths to reset')
})

stuck_scan_recovery_model = api.model('StuckScanRecovery', {
    'message': fields.String(description='Recovery status message'),
    'stuck_files_reset': fields.Integer(description='Number of stuck files that were reset')
})

reset_incomplete_scans_model = api.model('ResetIncompleteScans', {
    'message': fields.String(description='Result message'),
    'reset_count': fields.Integer(description='Number of files reset to pending'),
    'description': fields.String(description='Description of what was fixed')
})

# Authentication endpoints documentation
@auth_ns.route('/status')
class AuthStatus(Resource):
    @auth_ns.doc('Check authentication status')
    @auth_ns.marshal_with(auth_status_model)
    def get(self):
        '''Check current authentication status and first-run status'''
        pass

@auth_ns.route('/login')
class Login(Resource):
    @auth_ns.doc('User login')
    @auth_ns.expect(login_model)
    @auth_ns.marshal_with(login_response_model)
    def post(self):
        '''Authenticate user and create session'''
        pass

@auth_ns.route('/logout')
class Logout(Resource):
    @auth_ns.doc('User logout', security='apikey')
    @auth_ns.marshal_with(success_model)
    def post(self):
        '''Logout current user and invalidate session'''
        pass

@auth_ns.route('/setup')
class FirstRunSetup(Resource):
    @auth_ns.doc('First-run setup')
    @auth_ns.expect(api.model('FirstRunSetup', {
        'password': fields.String(required=True, description='Admin password')
    }))
    @auth_ns.marshal_with(success_model)
    def post(self):
        '''Set up initial admin password on first run'''
        pass

@auth_ns.route('/users')
class UserList(Resource):
    @auth_ns.doc('List users', security='apikey')
    @auth_ns.marshal_list_with(user_model)
    def get(self):
        '''Get list of all users (admin only)'''
        pass

    @auth_ns.doc('Create user', security='apikey')
    @auth_ns.expect(create_user_model)
    @auth_ns.marshal_with(user_model)
    def post(self):
        '''Create a new user (admin only)'''
        pass

@auth_ns.route('/users/<int:user_id>')
@auth_ns.param('user_id', 'User ID')
class UserDetail(Resource):
    @auth_ns.doc('Delete user', security='apikey')
    @auth_ns.marshal_with(success_model)
    def delete(self, user_id):
        '''Delete a user (admin only)'''
        pass

@auth_ns.route('/users/<int:user_id>/password')
@auth_ns.param('user_id', 'User ID')
class PasswordChange(Resource):
    @auth_ns.doc('Change password', security='apikey')
    @auth_ns.expect(password_change_model)
    @auth_ns.marshal_with(success_model)
    def put(self, user_id):
        '''Change user password'''
        pass

@auth_ns.route('/tokens')
class TokenList(Resource):
    @auth_ns.doc('List API tokens', security='apikey')
    @auth_ns.marshal_list_with(api_token_model)
    def get(self):
        '''Get list of user API tokens'''
        pass

    @auth_ns.doc('Create API token', security='apikey')
    @auth_ns.expect(create_token_model)
    @auth_ns.marshal_with(token_response_model)
    def post(self):
        '''Create a new API token'''
        pass

@auth_ns.route('/tokens/<int:token_id>')
@auth_ns.param('token_id', 'Token ID')
class TokenDetail(Resource):
    @auth_ns.doc('Delete API token', security='apikey')
    @auth_ns.marshal_with(success_model)
    def delete(self, token_id):
        '''Delete an API token'''
        pass

# Route implementations will be imported in app.py after blueprint registration
from flask import Blueprint, request, jsonify
import os
import json
import logging
from datetime import datetime, timezone
import pytz

from models import db, ScanResult, IgnoredErrorPattern, ScanConfiguration, ScanSchedule
from scheduler import MediaScheduler
from pixelprobe.utils.security import validate_json_input, AuditLogger, validate_directory_path

logger = logging.getLogger(__name__)

# Get timezone from environment variable, default to UTC
APP_TIMEZONE = os.environ.get('TZ', 'UTC')
try:
    tz = pytz.timezone(APP_TIMEZONE)
except pytz.exceptions.UnknownTimeZoneError:
    tz = pytz.UTC
    logger.warning(f"Unknown timezone '{APP_TIMEZONE}', falling back to UTC")

admin_bp = Blueprint('admin', __name__, url_prefix='/api')

# Import limiter from main app
from flask import current_app
from functools import wraps

# Create rate limit decorators that work with Flask-Limiter
def rate_limit(limit_string):
    """Decorator to apply rate limits using the app's limiter"""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            # Get the limiter from the current app
            limiter = current_app.extensions.get('flask-limiter')
            if limiter:
                # Apply the rate limit dynamically
                limited_func = limiter.limit(limit_string, exempt_when=lambda: False)(f)
                return limited_func(*args, **kwargs)
            else:
                # If no limiter, just call the function
                return f(*args, **kwargs)
        return wrapped
    return decorator

# Get scheduler instance (will be initialized in app context)
scheduler = None

def set_scheduler(sched):
    """Set the scheduler instance"""
    global scheduler
    scheduler = sched

@admin_bp.route('/mark-as-good', methods=['POST'])
@rate_limit("10 per minute")
@validate_json_input({
    'file_ids': {'required': True, 'type': list}
})
def mark_as_good():
    """Mark files as good/healthy"""
    data = request.get_json()
    file_ids = data.get('file_ids', [])
    
    # Validate file IDs are integers
    try:
        file_ids = [int(fid) for fid in file_ids]
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid file ID format'}), 400
    
    if len(file_ids) > 1000:  # Prevent excessive updates
        return jsonify({'error': 'Too many file IDs (max 1000)'}), 400
    
    try:
        for file_id in file_ids:
            result = ScanResult.query.get(file_id)
            if result:
                result.marked_as_good = True
                result.is_corrupted = False
                logger.info(f"Marked file as good (healthy): {result.file_path}")
                AuditLogger.log_action('mark_as_good', {'file_id': file_id, 'file_path': result.file_path})
            
        db.session.commit()
        logger.info(f"Successfully marked {len(file_ids)} files as good")
        
        return jsonify({
            'message': f'Successfully marked {len(file_ids)} files as good',
            'marked_files': len(file_ids)
        })
        
    except Exception as e:
        logger.error(f"Error marking files as good: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/ignored-patterns')
def get_ignored_patterns():
    """Get all ignored error patterns"""
    patterns = IgnoredErrorPattern.query.filter_by(is_active=True).all()
    return jsonify([{
        'id': p.id,
        'pattern': p.pattern,
        'description': p.description,
        'created_at': p.created_at.isoformat() if p.created_at else None
    } for p in patterns])

@admin_bp.route('/ignored-patterns', methods=['POST'])
@validate_json_input({
    'pattern': {'required': True, 'type': str, 'max_length': 200},
    'description': {'required': False, 'type': str, 'max_length': 500}
})
def add_ignored_pattern():
    """Add a new ignored error pattern"""
    data = request.get_json()
    pattern = data.get('pattern')
    description = data.get('description', '')
    
    # Validate pattern doesn't contain dangerous regex
    dangerous_patterns = [r'\(\?[imsxXU]', r'\(\?P<', r'\(\?#']
    for dp in dangerous_patterns:
        if dp in pattern:
            return jsonify({'error': 'Pattern contains potentially dangerous regex syntax'}), 400
    
    try:
        # Check for duplicate pattern
        existing = IgnoredErrorPattern.query.filter_by(pattern=pattern, is_active=True).first()
        if existing:
            return jsonify({'error': f'Pattern "{pattern}" already exists'}), 400
        
        new_pattern = IgnoredErrorPattern(
            pattern=pattern,
            description=description,
            is_active=True,
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(new_pattern)
        db.session.commit()
        
        AuditLogger.log_action('add_ignored_pattern', {'pattern': pattern})
        
        return jsonify({
            'id': new_pattern.id,
            'pattern': new_pattern.pattern,
            'description': new_pattern.description,
            'message': 'Pattern added successfully'
        }), 201
    except Exception as e:
        logger.error(f"Error adding ignored pattern: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/ignored-patterns/<int:pattern_id>', methods=['DELETE'])
def delete_ignored_pattern(pattern_id):
    """Delete an ignored error pattern"""
    pattern = IgnoredErrorPattern.query.get(pattern_id)
    if not pattern:
        return jsonify({'error': 'Pattern not found'}), 404
    
    try:
        pattern_text = pattern.pattern
        pattern.is_active = False  # Soft delete
        db.session.commit()
        
        AuditLogger.log_action('delete_ignored_pattern', {'pattern_id': pattern_id, 'pattern': pattern_text})
        
        return jsonify({'message': 'Pattern deleted successfully'})
    except Exception as e:
        logger.error(f"Error deleting ignored pattern: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/configurations')
def get_configurations():
    """Get all scan configurations"""
    configs = ScanConfiguration.query.all()
    return jsonify([{
        'id': c.id,
        'path': c.path,
        'is_active': c.is_active,
        'created_at': c.created_at.isoformat() if c.created_at else None
    } for c in configs])

@admin_bp.route('/configurations', methods=['POST'])
@validate_json_input({
    'path': {'required': True, 'type': str, 'max_length': 1000}
})
def add_configuration():
    """Add or update a scan configuration"""
    data = request.get_json()
    path = data.get('path')
    
    # Validate and normalize path
    try:
        path = validate_directory_path(path)
        AuditLogger.log_action('add_configuration', {'path': path})
    except Exception as e:
        AuditLogger.log_security_event('invalid_directory_path', str(e), 'warning')
        return jsonify({'error': 'Invalid directory path'}), 400
    
    try:
        # Check if configuration already exists
        existing_config = ScanConfiguration.query.filter_by(path=path).first()
        
        if existing_config:
            # Reactivate if it was deactivated
            existing_config.is_active = True
            message = 'Configuration reactivated'
        else:
            # Create new configuration with backward compatibility
            new_config = ScanConfiguration(
                path=path,
                is_active=True,
                created_at=datetime.now(timezone.utc),
                # Add legacy fields to satisfy old schema
                key=f'scan_dir_{len(ScanConfiguration.query.all()) + 1}',
                value=path,
                description=f'Scan directory: {path}'
            )
            db.session.add(new_config)
            message = 'Configuration added successfully'
        
        db.session.commit()
        
        return jsonify({
            'path': path,
            'message': message
        })
    except Exception as e:
        logger.error(f"Error adding configuration: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/schedules', methods=['GET'])
def get_schedules():
    """Get all scan schedules"""
    schedules = ScanSchedule.query.filter_by(is_active=True).all()
    return jsonify([schedule.to_dict() for schedule in schedules])

@admin_bp.route('/schedules', methods=['POST'])
def create_schedule():
    """Create a new scan schedule"""
    data = request.get_json()
    
    try:
        # Check for duplicate name
        name = data.get('name', 'Unnamed Schedule')
        existing = ScanSchedule.query.filter_by(name=name, is_active=True).first()
        if existing:
            return jsonify({'error': f'Schedule with name "{name}" already exists'}), 400
        
        schedule = ScanSchedule(
            name=name,
            cron_expression=data['cron_expression'],
            scan_type=data.get('scan_type', 'full'),
            force_rescan=data.get('force_rescan', False),
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(schedule)
        db.session.commit()
        
        # Update scheduler
        if scheduler:
            scheduler.update_schedules()
        
        return jsonify(schedule.to_dict()), 201
    except Exception as e:
        logger.error(f"Error creating schedule: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/schedules/<int:schedule_id>', methods=['PUT'])
def update_schedule(schedule_id):
    """Update a scan schedule"""
    schedule = ScanSchedule.query.get_or_404(schedule_id)
    data = request.get_json()
    
    try:
        schedule.name = data.get('name', schedule.name)
        schedule.cron_expression = data.get('cron_expression', schedule.cron_expression)
        schedule.scan_type = data.get('scan_type', schedule.scan_type)
        schedule.force_rescan = data.get('force_rescan', schedule.force_rescan)
        schedule.is_active = data.get('is_active', schedule.is_active)
        
        db.session.commit()
        
        # Update scheduler
        if scheduler:
            scheduler.update_schedules()
        
        return jsonify(schedule.to_dict())
    except Exception as e:
        logger.error(f"Error updating schedule: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/schedules/<int:schedule_id>', methods=['DELETE'])
def delete_schedule(schedule_id):
    """Delete a scan schedule"""
    schedule = ScanSchedule.query.get_or_404(schedule_id)
    
    try:
        schedule.is_active = False  # Soft delete
        db.session.commit()
        
        # Update scheduler
        if scheduler:
            scheduler.update_schedules()
        
        return '', 204
    except Exception as e:
        logger.error(f"Error deleting schedule: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/exclusions', methods=['GET'])
def get_exclusions():
    """Get current exclusion settings"""
    exclusions_file = os.path.join(os.path.dirname(__file__), '..', '..', 'exclusions.json')
    
    try:
        if os.path.exists(exclusions_file):
            with open(exclusions_file, 'r') as f:
                return jsonify(json.load(f))
        else:
            return jsonify({'paths': [], 'extensions': []})
    except Exception as e:
        logger.error(f"Error reading exclusions: {e}")
        return jsonify({'paths': [], 'extensions': []})

@admin_bp.route('/exclusions', methods=['PUT'])
def update_exclusions():
    """Update all exclusion settings"""
    data = request.get_json()
    exclusions_file = os.path.join(os.path.dirname(__file__), '..', '..', 'exclusions.json')
    
    try:
        # Validate data structure
        if not isinstance(data.get('paths', []), list) or not isinstance(data.get('extensions', []), list):
            return jsonify({'error': 'Invalid data format'}), 400
        
        # Write to file
        with open(exclusions_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        return jsonify({'message': 'Exclusions updated successfully'})
    except Exception as e:
        logger.error(f"Error updating exclusions: {e}")
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/exclusions/<exclusion_type>', methods=['POST'])
def add_exclusion(exclusion_type):
    """Add a single exclusion (path or extension)"""
    # Map singular to plural for storage
    type_map = {'path': 'paths', 'extension': 'extensions'}
    storage_type = type_map.get(exclusion_type)
    
    if not storage_type:
        return jsonify({'error': 'Invalid exclusion type'}), 400
    
    data = request.get_json()
    value = data.get('item') or data.get('value')  # Support both 'item' and 'value'
    
    if not value:
        return jsonify({'error': 'Value is required'}), 400
    
    exclusions_file = os.path.join(os.path.dirname(__file__), '..', '..', 'exclusions.json')
    
    try:
        # Read current exclusions
        exclusions = {'paths': [], 'extensions': []}
        if os.path.exists(exclusions_file):
            with open(exclusions_file, 'r') as f:
                exclusions = json.load(f)
        
        # Add new exclusion if not already present
        if value not in exclusions[storage_type]:
            exclusions[storage_type].append(value)
            
            # Write back to file
            with open(exclusions_file, 'w') as f:
                json.dump(exclusions, f, indent=2)
            
            return jsonify({'message': f'{exclusion_type.capitalize()} added successfully'})
        else:
            return jsonify({'message': f'{exclusion_type.capitalize()} already exists'}), 400
            
    except Exception as e:
        logger.error(f"Error adding exclusion: {e}")
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/exclusions/<exclusion_type>', methods=['DELETE'])
def remove_exclusion(exclusion_type):
    """Remove a single exclusion (path or extension)"""
    # Map singular to plural for storage
    type_map = {'path': 'paths', 'extension': 'extensions'}
    storage_type = type_map.get(exclusion_type)
    
    if not storage_type:
        return jsonify({'error': 'Invalid exclusion type'}), 400
    
    data = request.get_json()
    value = data.get('item') or data.get('value')  # Support both 'item' and 'value'
    
    if not value:
        return jsonify({'error': 'Value is required'}), 400
    
    exclusions_file = os.path.join(os.path.dirname(__file__), '..', '..', 'exclusions.json')
    
    try:
        # Read current exclusions
        exclusions = {'paths': [], 'extensions': []}
        if os.path.exists(exclusions_file):
            with open(exclusions_file, 'r') as f:
                exclusions = json.load(f)
        
        # Remove exclusion if present
        if value in exclusions[storage_type]:
            exclusions[storage_type].remove(value)
            
            # Write back to file
            with open(exclusions_file, 'w') as f:
                json.dump(exclusions, f, indent=2)
            
            return jsonify({'message': f'{exclusion_type.capitalize()} removed successfully'})
        else:
            return jsonify({'error': f'{exclusion_type.capitalize()} not found'}), 404
            
    except Exception as e:
        logger.error(f"Error removing exclusion: {e}")
        return jsonify({'error': str(e)}), 500
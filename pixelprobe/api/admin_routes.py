from flask import Blueprint, request, jsonify
import os
import json
import logging
from datetime import datetime, timezone, timedelta

from apscheduler.triggers.cron import CronTrigger

from pixelprobe.models import db, ScanResult, IgnoredErrorPattern, ScanConfiguration, ScanSchedule, AppConfig
from pixelprobe.constants import SETTING_GROUPS, SCANNER_SETTINGS_BY_KEY
from pixelprobe.services.settings_service import (describe_settings, coerce_setting,
                                                  invalidate_cache, plain_bound,
                                                  SettingValueError)
from pixelprobe.scheduler import MediaScheduler
from pixelprobe.utils.overrides import classify_findings, encode_verdict
from pixelprobe.utils.security import validate_json_input, AuditLogger, validate_directory_path
from pixelprobe.utils.validators import validate_time_budget
from pixelprobe.utils.integrity import adopt_bitrot_baseline
from pixelprobe.auth import auth_required

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__, url_prefix='/api')

from flask import current_app
from pixelprobe.utils.rate_limiting import rate_limit

# Get scheduler instance (will be initialized in app context)
scheduler = None

def set_scheduler(sched):
    """Set the scheduler instance"""
    global scheduler
    scheduler = sched


def calculate_next_run(cron_expression: str, last_run=None):
    """
    Calculate next run time from cron/interval expression.

    Works without requiring a running APScheduler instance, allowing any
    Gunicorn worker to calculate the correct next_run time.

    Args:
        cron_expression: Either a cron expression (e.g., "*/5 * * * *")
                        or interval format (e.g., "interval:hours:6")
        last_run: Optional last run time (used for interval calculations)

    Returns:
        Next run time as timezone-aware datetime (UTC)
    """
    now = datetime.now(timezone.utc)

    if cron_expression.startswith('interval:'):
        # Parse interval format: interval:unit:value
        parts = cron_expression.split(':')
        if len(parts) == 3:
            unit = parts[1]
            value = int(parts[2])
            interval = timedelta(**{unit: value})

            if last_run:
                # Ensure timezone-aware
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=timezone.utc)
                next_time = last_run + interval
                # If calculated time is in past, schedule from now
                if next_time < now:
                    next_time = now + interval
                return next_time
            return now + interval
        raise ValueError(f"Invalid interval format: {cron_expression}")
    else:
        # Standard cron format - use APScheduler's trigger
        parts = cron_expression.split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {cron_expression}")
        trigger = CronTrigger(
            minute=parts[0], hour=parts[1], day=parts[2],
            month=parts[3], day_of_week=parts[4],
            timezone='UTC'
        )
        return trigger.get_next_fire_time(None, now)


def _parse_file_ids(data):
    """Coerce and bound the file_ids list shared by batch file endpoints.

    Returns (file_ids, error_response).
    """
    try:
        file_ids = [int(fid) for fid in data.get('file_ids', [])]
    except (ValueError, TypeError):
        return None, ({'error': 'Invalid file ID format'}, 400)
    if len(file_ids) > 1000:  # Prevent excessive updates
        return None, ({'error': 'Too many file IDs (max 1000)'}, 400)
    return file_ids, None


@admin_bp.route('/mark-as-good', methods=['POST'])
@rate_limit("10 per minute")
@auth_required
@validate_json_input({
    'file_ids': {'required': True, 'type': list}
})
def mark_as_good():
    """Mark files as good/healthy"""
    data = request.get_json()
    file_ids, id_error = _parse_file_ids(data)
    if id_error:
        return id_error

    try:
        for file_id in file_ids:
            result = db.session.get(ScanResult, file_id)
            if result:
                result.marked_as_good = True
                result.is_corrupted = False
                # Record what this judgement actually excused: this file
                # version, these finding classes. The scan write path retires
                # the override when either stops matching.
                result.marked_good_hash = result.file_hash
                result.marked_good_date = datetime.now(timezone.utc)
                # A mark on a file with no findings excuses nothing: store a
                # sentinel rather than NULL, because NULL means excuse-everything
                # and is reserved for legacy rows whose reason is unknown.
                result.marked_good_verdict = encode_verdict(
                    classify_findings(result.corruption_details, result.warning_details)) or 'none'
                logger.info(f"Marked file as good (healthy): {result.file_path}")
                AuditLogger.log_action('mark_as_good', {'file_id': file_id, 'file_path': result.file_path})
            
        db.session.commit()
        logger.info(f"Successfully marked {len(file_ids)} files as good")
        
        return {
            'message': f'Successfully marked {len(file_ids)} files as good',
            'marked_files': len(file_ids)
        }
        
    except Exception as e:
        logger.error(f"Error marking files as good: {str(e)}", exc_info=True)
        db.session.rollback()
        return {'error': 'Internal server error'}, 500

@admin_bp.route('/bitrot/accept', methods=['POST'])
@rate_limit("10 per minute")
@auth_required
@validate_json_input({
    'file_ids': {'required': True, 'type': list}
})
def accept_bitrot_current_state():
    """Accept a bitrot-suspected file's current content as the new baseline.

    Adopts the candidate hash and current on-disk mtime, clears the flag and
    stability counter. bitrot_detected_date/bitrot_details are preserved so
    files that ever tripped bitrot stay queryable (dying-disk signal).
    """
    data = request.get_json()
    file_ids, id_error = _parse_file_ids(data)
    if id_error:
        return id_error

    try:
        rows = ScanResult.query.filter(ScanResult.id.in_(file_ids)).all() if file_ids else []
        rows_by_id = {row.id: row for row in rows}
        accepted = 0
        skipped = []
        for file_id in file_ids:
            result = rows_by_id.get(file_id)
            if not result or not result.bitrot_suspected:
                skipped.append(file_id)
                continue

            # Baseline mtime: the one recorded when the candidate hash was
            # computed. A fresh os.stat could pair the candidate hash with a
            # NEWER mtime if the file changed again after the last check,
            # storing a baseline pair that never described real content. If
            # no recorded mtime exists the row re-baselines its mtime on the
            # next hash-match integrity check.
            mtime = None
            try:
                details = json.loads(result.bitrot_details or '{}')
                if details.get('current_modified'):
                    mtime = datetime.fromisoformat(details['current_modified'])
            except (ValueError, TypeError):
                mtime = None

            adopt_bitrot_baseline(result, mtime)
            accepted += 1
            logger.info(f"Accepted current state for bitrot-suspected file: {result.file_path}")
            AuditLogger.log_action('bitrot_accept', {'file_id': file_id, 'file_path': result.file_path})

        db.session.commit()
        return {
            'message': f'Accepted current state for {accepted} file(s)',
            'accepted': accepted,
            'skipped': skipped
        }
    except Exception as e:
        logger.error(f"Error accepting bitrot state: {str(e)}", exc_info=True)
        db.session.rollback()
        return {'error': 'Internal server error'}, 500


@admin_bp.route('/ignored-patterns')
@auth_required
def get_ignored_patterns():
    """Get all ignored error patterns"""
    patterns = IgnoredErrorPattern.query.filter_by(is_active=True).all()
    return [{
        'id': p.id,
        'pattern': p.pattern,
        'description': p.description,
        'created_at': p.created_at.isoformat() if p.created_at else None
    } for p in patterns]

@admin_bp.route('/ignored-patterns', methods=['POST'])
@auth_required
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
            return {'error': 'Pattern contains potentially dangerous regex syntax'}, 400
    
    try:
        # Check for duplicate pattern
        existing = IgnoredErrorPattern.query.filter_by(pattern=pattern, is_active=True).first()
        if existing:
            # Same reason as create_schedule: don't reflect caller input.
            return {'error': 'That pattern already exists'}, 400
        
        new_pattern = IgnoredErrorPattern(
            pattern=pattern,
            description=description,
            is_active=True,
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(new_pattern)
        db.session.commit()
        
        AuditLogger.log_action('add_ignored_pattern', {'pattern': pattern})
        
        return {
            'id': new_pattern.id,
            'pattern': new_pattern.pattern,
            'description': new_pattern.description,
            'message': 'Pattern added successfully'
        }, 201
    except Exception as e:
        logger.error(f"Error adding ignored pattern: {e}", exc_info=True)
        db.session.rollback()
        return {'error': 'Internal server error'}, 500

@admin_bp.route('/ignored-patterns/<int:pattern_id>', methods=['DELETE'])
@auth_required
def delete_ignored_pattern(pattern_id):
    """Delete an ignored error pattern"""
    pattern = db.session.get(IgnoredErrorPattern, pattern_id)
    if not pattern:
        return {'error': 'Pattern not found'}, 404
    
    try:
        pattern_text = pattern.pattern
        pattern.is_active = False  # Soft delete
        db.session.commit()
        
        AuditLogger.log_action('delete_ignored_pattern', {'pattern_id': pattern_id, 'pattern': pattern_text})
        
        return {'message': 'Pattern deleted successfully'}
    except Exception as e:
        logger.error(f"Error deleting ignored pattern: {e}", exc_info=True)
        db.session.rollback()
        return {'error': 'Internal server error'}, 500

@admin_bp.route('/configurations')
@auth_required
def get_configurations():
    """Get all scan configurations"""
    configs = ScanConfiguration.query.all()
    return [{
        'id': c.id,
        'path': c.path,
        'is_active': c.is_active,
        'created_at': c.created_at.isoformat() if c.created_at else None
    } for c in configs]

@admin_bp.route('/configurations', methods=['POST'])
@auth_required
@validate_json_input({
    'path': {'required': True, 'type': str, 'max_length': 1000}
})
def add_configuration():
    """Add or update a scan configuration"""
    data = request.get_json()
    path = data.get('path')
    
    # Admin is defining a new allowlist entry, so skip the allowlist check.
    # Traversal tokens and symlink resolution still run.
    try:
        path = validate_directory_path(path, allowed_paths=[])
        AuditLogger.log_action('add_configuration', {'path': path})
    except Exception as e:
        AuditLogger.log_security_event('invalid_directory_path', str(e), 'warning')
        return {'error': 'Invalid directory path'}, 400
    
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
        
        return {
            'path': path,
            'message': message
        }
    except Exception as e:
        logger.error(f"Error adding configuration: {e}", exc_info=True)
        db.session.rollback()
        return {'error': 'Internal server error'}, 500

@admin_bp.route('/schedules', methods=['GET'])
@auth_required
def get_schedules():
    """Get all scan schedules"""
    # Return all schedules (active and inactive) so they can be toggled
    # DELETE endpoint does hard delete, so truly deleted ones won't appear
    schedules = ScanSchedule.query.all()
    return {'schedules': [schedule.to_dict() for schedule in schedules]}

@admin_bp.route('/schedules/<int:schedule_id>', methods=['GET'])
@auth_required
def get_schedule(schedule_id):
    """Get a specific scan schedule by ID"""
    schedule = db.get_or_404(ScanSchedule, schedule_id)
    return jsonify(schedule.to_dict())

def _validate_time_budget(data, scan_type):
    """Validate time_budget_minutes from a schedule payload. Returns (value, error_response)."""
    value, error = validate_time_budget(data.get('time_budget_minutes'), scan_type)
    if error:
        return None, ({'error': error}, 400)
    return value, None


@admin_bp.route('/schedules', methods=['POST'])
@auth_required
def create_schedule():
    """Create a new scan schedule"""
    data = request.get_json()

    try:
        # Check for duplicate name
        name = data.get('name', 'Unnamed Schedule')
        existing = ScanSchedule.query.filter_by(name=name, is_active=True).first()
        if existing:
            # Name is not echoed back: reflecting caller input into the response
            # body is the taint flow CodeQL rejects. The caller supplied it.
            return {'error': 'A schedule with that name already exists'}, 400

        scan_type = data.get('scan_type', 'full')
        time_budget, budget_error = _validate_time_budget(data, scan_type)
        if budget_error:
            return budget_error

        schedule = ScanSchedule(
            name=name,
            cron_expression=data['cron_expression'],
            scan_paths=json.dumps(data.get('scan_paths', [])),
            scan_type=scan_type,
            force_rescan=data.get('force_rescan', False),
            time_budget_minutes=time_budget,
            is_active=True,
            created_at=datetime.now(timezone.utc)
        )
        # Populate next_run immediately so the UI shows it before the first
        # fire (the scheduler's db-sync job registers the actual APScheduler
        # job within a minute)
        try:
            schedule.next_run = calculate_next_run(schedule.cron_expression)
        except Exception as e:
            logger.warning(f"Could not calculate next_run for new schedule: {e}")
        db.session.add(schedule)
        db.session.commit()

        # Trigger schedule reload in Celery worker (where scheduler runs)
        # Import lazily to avoid circular import (tasks.py -> app.py -> admin_routes.py)
        try:
            from pixelprobe.tasks import reload_schedules_task
            reload_schedules_task.delay()
        except Exception as e:
            # May fail if Celery/Redis unavailable (ImportError, ConnectionError, etc.)
            logger.warning(f"Could not trigger schedule reload: {e}")

        return schedule.to_dict(), 201
    except Exception as e:
        logger.error(f"Error creating schedule: {e}", exc_info=True)
        db.session.rollback()
        return {'error': 'Internal server error'}, 500

@admin_bp.route('/schedules/<int:schedule_id>', methods=['PUT'])
@auth_required
def update_schedule(schedule_id):
    """Update a scan schedule"""
    schedule = db.get_or_404(ScanSchedule, schedule_id)
    data = request.get_json()

    try:
        # Track if schedule is being re-enabled or cron changed
        was_inactive = not schedule.is_active
        new_is_active = data.get('is_active', schedule.is_active)
        being_reactivated = was_inactive and new_is_active

        new_cron = data.get('cron_expression', schedule.cron_expression)
        cron_changed = new_cron != schedule.cron_expression

        new_scan_type = data.get('scan_type', schedule.scan_type)
        if 'time_budget_minutes' in data:
            time_budget, budget_error = _validate_time_budget(data, new_scan_type)
            if budget_error:
                return budget_error
            schedule.time_budget_minutes = time_budget
        elif new_scan_type != 'file_changes' and schedule.time_budget_minutes is not None:
            # Type changed away from file_changes: the budget no longer applies
            logger.info(f"Clearing time_budget_minutes on schedule {schedule_id} (scan_type now {new_scan_type})")
            schedule.time_budget_minutes = None

        # Update fields
        schedule.name = data.get('name', schedule.name)
        schedule.cron_expression = new_cron
        if 'scan_paths' in data:
            schedule.scan_paths = json.dumps(data['scan_paths'])
        schedule.scan_type = new_scan_type
        schedule.force_rescan = data.get('force_rescan', schedule.force_rescan)
        schedule.is_active = new_is_active

        # Recalculate next_run when:
        # 1. Schedule is being re-enabled, OR
        # 2. Cron expression changed while schedule is active
        if being_reactivated or (cron_changed and new_is_active):
            try:
                schedule.next_run = calculate_next_run(schedule.cron_expression, schedule.last_run)
                logger.info(f"Recalculated next_run for schedule {schedule_id}: {schedule.next_run}")
            except Exception as e:
                logger.warning(f"Could not calculate next_run for schedule {schedule_id}: {e}")

        db.session.commit()

        # Trigger schedule reload in Celery worker (where scheduler runs)
        try:
            from pixelprobe.tasks import reload_schedules_task
            reload_schedules_task.delay()
        except Exception as e:
            logger.warning(f"Could not trigger schedule reload: {e}")

        return schedule.to_dict()
    except Exception as e:
        logger.error(f"Error updating schedule: {e}", exc_info=True)
        db.session.rollback()
        return {'error': 'Internal server error'}, 500

@admin_bp.route('/schedules/<int:schedule_id>', methods=['DELETE'])
@auth_required
def delete_schedule(schedule_id):
    """Delete a scan schedule"""
    schedule = db.get_or_404(ScanSchedule, schedule_id)
    
    try:
        # Actually delete the schedule from database instead of soft delete
        db.session.delete(schedule)
        db.session.commit()

        # Trigger schedule reload in Celery worker (where scheduler runs)
        try:
            from pixelprobe.tasks import reload_schedules_task
            reload_schedules_task.delay()
        except Exception as e:
            logger.warning(f"Could not trigger schedule reload: {e}")

        return '', 204
    except Exception as e:
        logger.error(f"Error deleting schedule: {e}", exc_info=True)
        db.session.rollback()
        return {'error': 'Internal server error'}, 500

@admin_bp.route('/exclusions', methods=['GET'])
@auth_required
def get_exclusions():
    """Get current exclusion settings from database"""
    try:
        from pixelprobe.models import Exclusion
        
        # Get all active exclusions
        path_exclusions = Exclusion.query.filter_by(
            exclusion_type='path', 
            is_active=True
        ).all()
        
        extension_exclusions = Exclusion.query.filter_by(
            exclusion_type='extension', 
            is_active=True
        ).all()
        
        return {
            'paths': [e.value for e in path_exclusions],
            'extensions': [e.value for e in extension_exclusions]
        }
    except Exception as e:
        logger.error(f"Error reading exclusions: {e}")
        return {'paths': [], 'extensions': []}

@admin_bp.route('/exclusions', methods=['PUT'])
@auth_required
def update_exclusions():
    """Update all exclusion settings in database"""
    data = request.get_json()
    
    try:
        from pixelprobe.models import Exclusion
        
        # Validate data structure
        if not isinstance(data.get('paths', []), list) or not isinstance(data.get('extensions', []), list):
            return {'error': 'Invalid data format'}, 400
        
        # Clear existing exclusions
        Exclusion.query.update({'is_active': False})
        
        # Add new exclusions
        for path in data.get('paths', []):
            exclusion = Exclusion(exclusion_type='path', value=path, is_active=True)
            db.session.add(exclusion)
        
        for extension in data.get('extensions', []):
            exclusion = Exclusion(exclusion_type='extension', value=extension, is_active=True)
            db.session.add(exclusion)
        
        db.session.commit()
        return {'message': 'Exclusions updated successfully'}
    except Exception as e:
        logger.error(f"Error updating exclusions: {e}", exc_info=True)
        db.session.rollback()
        return {'error': 'Internal server error'}, 500

@admin_bp.route('/exclusions/<exclusion_type>', methods=['POST'])
@auth_required
def add_exclusion(exclusion_type):
    """Add a single exclusion (path or extension) to database"""
    # Validate exclusion type
    if exclusion_type not in ['path', 'extension']:
        return {'error': 'Invalid exclusion type'}, 400
    
    data = request.get_json()
    value = data.get('item') or data.get('value')  # Support both 'item' and 'value'
    
    if not value:
        return {'error': 'Value is required'}, 400
    
    try:
        from pixelprobe.models import Exclusion
        
        # Check if already exists
        existing = Exclusion.query.filter_by(
            exclusion_type=exclusion_type,
            value=value,
            is_active=True
        ).first()
        
        if existing:
            return {'error': f'{exclusion_type.capitalize()} already exists'}, 400

        # Add new exclusion
        exclusion = Exclusion(
            exclusion_type=exclusion_type,
            value=value,
            is_active=True
        )
        db.session.add(exclusion)
        db.session.commit()

        AuditLogger.log_action('add_exclusion', {'type': exclusion_type, 'value': value})

        return {'message': f'{exclusion_type.capitalize()} added successfully'}
            
    except Exception as e:
        logger.error(f"Error adding exclusion: {e}", exc_info=True)
        db.session.rollback()
        return {'error': 'Internal server error'}, 500

@admin_bp.route('/exclusions/<exclusion_type>', methods=['DELETE'])
@auth_required
def remove_exclusion(exclusion_type):
    """Remove a single exclusion (path or extension) from database"""
    # Validate exclusion type
    if exclusion_type not in ['path', 'extension']:
        return {'error': 'Invalid exclusion type'}, 400
    
    data = request.get_json()
    value = data.get('item') or data.get('value')  # Support both 'item' and 'value'
    
    if not value:
        return {'error': 'Value is required'}, 400
    
    try:
        from pixelprobe.models import Exclusion
        
        # Find the exclusion
        exclusion = Exclusion.query.filter_by(
            exclusion_type=exclusion_type,
            value=value,
            is_active=True
        ).first()

        if not exclusion:
            return {'error': f'{exclusion_type.capitalize()} not found'}, 404

        # Soft delete
        exclusion.is_active = False
        db.session.commit()

        AuditLogger.log_action('remove_exclusion', {'type': exclusion_type, 'value': value})

        return {'message': f'{exclusion_type.capitalize()} removed successfully'}
            
    except Exception as e:
        logger.error(f"Error removing exclusion: {e}", exc_info=True)
        db.session.rollback()
        return {'error': 'Internal server error'}, 500

def _rejection_message(spec):
    """Why a value was rejected, phrased from the registry alone.

    Deliberately built without touching what the caller sent. A validation
    error must not reflect request data back into the response (CodeQL
    py/reflective-xss), and stating the accepted range is more use to whoever
    typed it than repeating what they typed.
    """
    label = spec['label']
    if spec['type'] == 'bool':
        return f"{label} must be true or false"

    kind = 'a whole number' if spec['type'] == 'int' else 'a number'
    low, high = spec.get('min'), spec.get('max')
    if low is not None and high is not None:
        return f"{label} must be {kind} between {plain_bound(low)} and {plain_bound(high)}"
    if low is not None:
        return f"{label} must be {kind} of {plain_bound(low)} or more"
    if high is not None:
        return f"{label} must be {kind} of {plain_bound(high)} or less"
    return f"{label} must be {kind}"


@admin_bp.route('/settings', methods=['GET'])
@auth_required
def get_settings():
    """Every scanner setting with its current value, grouped for display."""
    described = describe_settings()
    return {
        'groups': [{
            'key': group['key'],
            'label': group['label'],
            'help': group['help'],
            'settings': [s for s in described if s['group'] == group['key']],
        } for group in SETTING_GROUPS]
    }


@admin_bp.route('/settings', methods=['PUT'])
@auth_required
def update_settings():
    """Save one or more settings.

    Every supplied value is validated before anything is written, so a bad
    value in the batch leaves the stored settings untouched rather than
    applying half of them.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return {'error': 'Send a JSON object of setting keys and values'}, 400

    if any(k not in SCANNER_SETTINGS_BY_KEY for k in data):
        # The supplied name is not echoed back: GET /api/settings lists the
        # valid keys, and reflecting request data is what CodeQL rejects.
        return {'error': 'Unknown setting. GET /api/settings lists the valid keys.'}, 400

    validated = {}
    for key, raw in data.items():
        spec = SCANNER_SETTINGS_BY_KEY[key]
        try:
            validated[key] = coerce_setting(spec, raw)
        except SettingValueError:
            return {'error': _rejection_message(spec)}, 400

    for key, value in validated.items():
        spec = SCANNER_SETTINGS_BY_KEY[key]
        stored = str(value).lower() if spec['type'] == 'bool' else str(value)
        row = AppConfig.query.filter_by(key=key).first()
        if row:
            row.value = stored
        else:
            db.session.add(AppConfig(key=key, value=stored, description=spec['label']))

    db.session.commit()
    invalidate_cache()
    AuditLogger.log_action('update_settings', {'keys': sorted(validated)})
    logger.info("Scanner settings updated: %s", sorted(validated))
    return {'updated': sorted(validated), 'settings': describe_settings()}


@admin_bp.route('/settings/<path:key>', methods=['DELETE'])
@auth_required
def reset_setting(key):
    """Restore one setting to its built-in default."""
    spec = SCANNER_SETTINGS_BY_KEY.get(key)
    if spec is None:
        return {'error': 'Unknown setting. GET /api/settings lists the valid keys.'}, 404

    row = AppConfig.query.filter_by(key=key).first()
    if row:
        db.session.delete(row)
        db.session.commit()
        invalidate_cache()
    AuditLogger.log_action('reset_setting', {'key': spec['key']})
    # The registry's own key, not the path segment the caller sent.
    return {'reset': spec['key'], 'settings': describe_settings()}

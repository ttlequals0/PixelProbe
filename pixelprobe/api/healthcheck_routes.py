"""
Healthcheck configuration API routes for PixelProbe
"""

from flask import Blueprint, request, jsonify
import logging
from datetime import datetime, timezone

from pixelprobe.models import db, HealthcheckConfig, ScanSchedule
from pixelprobe.auth import auth_required
from pixelprobe.services.healthcheck_service import HealthcheckService

logger = logging.getLogger(__name__)

healthcheck_bp = Blueprint('healthcheck', __name__, url_prefix='/api/healthcheck')


@healthcheck_bp.route('', methods=['GET'])
@auth_required
def get_all_healthcheck_configs():
    """Get all healthcheck configurations

    Returns:
        JSON list of all healthcheck configurations with their associated schedules
    """
    try:
        configs = HealthcheckConfig.query.all()

        result = []
        for config in configs:
            config_dict = config.to_dict()

            # Include schedule name for reference
            if config.schedule:
                config_dict['schedule_name'] = config.schedule.name

            result.append(config_dict)

        return jsonify(result), 200

    except Exception as e:
        logger.error(f"Error getting healthcheck configs: {e}")
        return jsonify({'error': 'Failed to get healthcheck configurations'}), 500


@healthcheck_bp.route('/<int:config_id>', methods=['GET'])
@auth_required
def get_healthcheck_config(config_id):
    """Get a specific healthcheck configuration

    Args:
        config_id: The healthcheck configuration ID

    Returns:
        JSON healthcheck configuration details
    """
    try:
        config = db.session.get(HealthcheckConfig, config_id)

        if not config:
            return jsonify({'error': 'Healthcheck configuration not found'}), 404

        config_dict = config.to_dict()

        # Include schedule details
        if config.schedule:
            config_dict['schedule_name'] = config.schedule.name
            config_dict['schedule_active'] = config.schedule.is_active

        return jsonify(config_dict), 200

    except Exception as e:
        logger.error(f"Error getting healthcheck config {config_id}: {e}")
        return jsonify({'error': 'Failed to get healthcheck configuration'}), 500


@healthcheck_bp.route('/schedule/<int:schedule_id>', methods=['GET'])
@auth_required
def get_healthcheck_by_schedule(schedule_id):
    """Get healthcheck configuration for a specific schedule

    Args:
        schedule_id: The scan schedule ID

    Returns:
        JSON healthcheck configuration or 404 if not found
    """
    try:
        config = HealthcheckConfig.query.filter_by(schedule_id=schedule_id).first()

        if not config:
            return jsonify({'error': 'No healthcheck configuration found for this schedule'}), 404

        config_dict = config.to_dict()

        # Include schedule details
        if config.schedule:
            config_dict['schedule_name'] = config.schedule.name

        return jsonify(config_dict), 200

    except Exception as e:
        logger.error(f"Error getting healthcheck config for schedule {schedule_id}: {e}")
        return jsonify({'error': 'Failed to get healthcheck configuration'}), 500


@healthcheck_bp.route('', methods=['POST'])
@auth_required
def create_healthcheck_config():
    """Create a new healthcheck configuration

    Request JSON:
        {
            "schedule_id": int,
            "healthcheck_url": str,
            "is_active": bool (optional, default: true),
            "send_start_ping": bool (optional, default: true),
            "send_success_ping": bool (optional, default: true),
            "send_failure_ping": bool (optional, default: true),
            "include_report_data": bool (optional, default: true)
        }

    Returns:
        JSON created healthcheck configuration
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Validate required fields
        if 'schedule_id' not in data:
            return jsonify({'error': 'schedule_id is required'}), 400

        if 'healthcheck_url' not in data:
            return jsonify({'error': 'healthcheck_url is required'}), 400

        schedule_id = data['schedule_id']
        healthcheck_url = data['healthcheck_url']

        # Verify schedule exists
        schedule = db.session.get(ScanSchedule, schedule_id)
        if not schedule:
            return jsonify({'error': f'Schedule {schedule_id} not found'}), 404

        # Check if healthcheck config already exists for this schedule
        existing_config = HealthcheckConfig.query.filter_by(schedule_id=schedule_id).first()
        if existing_config:
            return jsonify({'error': f'Healthcheck configuration already exists for schedule {schedule_id}'}), 409

        # Validate healthcheck URL
        healthcheck_service = HealthcheckService()
        is_valid, error_msg = healthcheck_service.validate_url(healthcheck_url)
        if not is_valid:
            return jsonify({'error': f'Invalid healthcheck URL: {error_msg}'}), 400

        # Create new config
        new_config = HealthcheckConfig(
            schedule_id=schedule_id,
            healthcheck_url=healthcheck_url,
            is_active=data.get('is_active', True),
            send_start_ping=data.get('send_start_ping', True),
            send_success_ping=data.get('send_success_ping', True),
            send_failure_ping=data.get('send_failure_ping', True),
            include_report_data=data.get('include_report_data', True)
        )

        db.session.add(new_config)
        db.session.commit()

        logger.info(f"Created healthcheck config {new_config.id} for schedule {schedule_id}")

        result = new_config.to_dict()
        result['schedule_name'] = schedule.name

        return jsonify(result), 201

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating healthcheck config: {e}")
        return jsonify({'error': 'Failed to create healthcheck configuration'}), 500


@healthcheck_bp.route('/<int:config_id>', methods=['PUT'])
@auth_required
def update_healthcheck_config(config_id):
    """Update an existing healthcheck configuration

    Args:
        config_id: The healthcheck configuration ID

    Request JSON (all fields optional):
        {
            "healthcheck_url": str,
            "is_active": bool,
            "send_start_ping": bool,
            "send_success_ping": bool,
            "send_failure_ping": bool,
            "include_report_data": bool
        }

    Returns:
        JSON updated healthcheck configuration
    """
    try:
        config = db.session.get(HealthcheckConfig, config_id)

        if not config:
            return jsonify({'error': 'Healthcheck configuration not found'}), 404

        data = request.get_json()

        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Update healthcheck URL if provided
        if 'healthcheck_url' in data:
            healthcheck_url = data['healthcheck_url']

            # Validate new URL
            healthcheck_service = HealthcheckService()
            is_valid, error_msg = healthcheck_service.validate_url(healthcheck_url)
            if not is_valid:
                return jsonify({'error': f'Invalid healthcheck URL: {error_msg}'}), 400

            config.healthcheck_url = healthcheck_url

        # Update other fields if provided
        if 'is_active' in data:
            config.is_active = bool(data['is_active'])

        if 'send_start_ping' in data:
            config.send_start_ping = bool(data['send_start_ping'])

        if 'send_success_ping' in data:
            config.send_success_ping = bool(data['send_success_ping'])

        if 'send_failure_ping' in data:
            config.send_failure_ping = bool(data['send_failure_ping'])

        if 'include_report_data' in data:
            config.include_report_data = bool(data['include_report_data'])

        config.updated_at = datetime.now(timezone.utc)
        db.session.commit()

        logger.info(f"Updated healthcheck config {config_id}")

        result = config.to_dict()
        if config.schedule:
            result['schedule_name'] = config.schedule.name

        return jsonify(result), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating healthcheck config {config_id}: {e}")
        return jsonify({'error': 'Failed to update healthcheck configuration'}), 500


@healthcheck_bp.route('/<int:config_id>', methods=['DELETE'])
@auth_required
def delete_healthcheck_config(config_id):
    """Delete a healthcheck configuration

    Args:
        config_id: The healthcheck configuration ID

    Returns:
        JSON success message
    """
    try:
        config = db.session.get(HealthcheckConfig, config_id)

        if not config:
            return jsonify({'error': 'Healthcheck configuration not found'}), 404

        schedule_id = config.schedule_id

        db.session.delete(config)
        db.session.commit()

        logger.info(f"Deleted healthcheck config {config_id} for schedule {schedule_id}")

        return jsonify({'message': 'Healthcheck configuration deleted successfully'}), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting healthcheck config {config_id}: {e}")
        return jsonify({'error': 'Failed to delete healthcheck configuration'}), 500


@healthcheck_bp.route('/<int:config_id>/test', methods=['POST'])
@auth_required
def test_healthcheck(config_id):
    """Test a healthcheck configuration by sending a ping

    Args:
        config_id: The healthcheck configuration ID

    Returns:
        JSON test result
    """
    try:
        config = db.session.get(HealthcheckConfig, config_id)

        if not config:
            return jsonify({'error': 'Healthcheck configuration not found'}), 404

        healthcheck_service = HealthcheckService()

        # Send a test ping (success ping with no data)
        success = healthcheck_service.ping_success(config.healthcheck_url, report_data=None)

        # Update ping status
        config.last_ping_status = 'success' if success else 'failure'
        config.last_ping_time = datetime.now(timezone.utc)
        db.session.commit()

        if success:
            logger.info(f"Test ping successful for healthcheck config {config_id}")
            return jsonify({
                'success': True,
                'message': 'Test ping sent successfully',
                'last_ping_time': config.last_ping_time.isoformat()
            }), 200
        else:
            logger.warning(f"Test ping failed for healthcheck config {config_id}")
            return jsonify({
                'success': False,
                'message': 'Test ping failed - check URL and network connectivity',
                'last_ping_time': config.last_ping_time.isoformat()
            }), 200

    except Exception as e:
        logger.error(f"Error testing healthcheck config {config_id}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to test healthcheck'}), 500

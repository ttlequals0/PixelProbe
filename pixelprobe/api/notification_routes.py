"""
Notification configuration API routes for PixelProbe
P3 audit fix: Add API endpoints for notification provider management
"""

from flask import Blueprint, request, jsonify
import logging
from datetime import datetime, timezone
from typing import Optional

from pixelprobe.models import db, NotificationProvider, NotificationRule
from pixelprobe.auth import auth_required
from pixelprobe.services.notification_service import (
    NotificationService, VALID_EMAIL_SECURITY, parse_recipients, resolve_smtp_port
)
from pixelprobe.utils.security import validate_safe_url, validate_outbound_host

logger = logging.getLogger(__name__)

notification_bp = Blueprint('notifications', __name__, url_prefix='/api/notifications')

# Valid provider types and event types
VALID_PROVIDER_TYPES = ['pushover', 'ntfy', 'webhook', 'email']
VALID_EVENT_TYPES = [
    'scan_start', 'scan_complete', 'scan_failed', 'scan_missed',
    'corruption_found', 'bitrot_suspected', 'user_added', 'user_deleted',
    'api_key_added', 'api_key_deleted', 'auth_failed'
]
VALID_PRIORITIES = ['low', 'normal', 'high']


# ==================== Provider Endpoints ====================

@notification_bp.route('/providers', methods=['GET'])
@auth_required
def get_providers():
    """Get all notification providers

    Returns:
        JSON list of all notification providers
    """
    try:
        providers = NotificationProvider.query.all()
        include_config = request.args.get('include_config', 'false').lower() == 'true'
        return jsonify([p.to_dict(include_config=include_config) for p in providers]), 200
    except Exception as e:
        logger.error(f"Error getting notification providers: {e}")
        return jsonify({'error': 'Failed to get notification providers'}), 500


@notification_bp.route('/providers/<int:provider_id>', methods=['GET'])
@auth_required
def get_provider(provider_id):
    """Get a specific notification provider

    Args:
        provider_id: The provider ID

    Returns:
        JSON provider details
    """
    try:
        provider = db.session.get(NotificationProvider, provider_id)
        if not provider:
            return jsonify({'error': 'Provider not found'}), 404

        include_config = request.args.get('include_config', 'false').lower() == 'true'
        return jsonify(provider.to_dict(include_config=include_config)), 200
    except Exception as e:
        logger.error(f"Error getting provider {provider_id}: {e}")
        return jsonify({'error': 'Failed to get provider'}), 500


@notification_bp.route('/providers', methods=['POST'])
@auth_required
def create_provider():
    """Create a new notification provider

    Request body:
        - name: Provider name (required)
        - provider_type: Type of provider - 'pushover', 'ntfy', 'webhook', 'email' (required)
        - configuration: Provider-specific config (required)
        - is_active: Whether provider is active (optional, default: true)

    Returns:
        JSON created provider
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Request body is required'}), 400

        # Validate required fields
        name = data.get('name')
        provider_type = data.get('provider_type')
        configuration = data.get('configuration')

        if not name:
            return jsonify({'error': 'Provider name is required'}), 400
        if not provider_type:
            return jsonify({'error': 'Provider type is required'}), 400
        if provider_type not in VALID_PROVIDER_TYPES:
            return jsonify({'error': f'Invalid provider type. Must be one of: {VALID_PROVIDER_TYPES}'}), 400
        if not configuration:
            return jsonify({'error': 'Configuration is required'}), 400

        # Validate provider-specific configuration
        validation_error = _validate_provider_config(provider_type, configuration)
        if validation_error:
            return jsonify({'error': validation_error}), 400

        provider = NotificationProvider(
            name=name,
            provider_type=provider_type,
            configuration=configuration,
            is_active=data.get('is_active', True)
        )

        db.session.add(provider)
        db.session.commit()

        logger.info(f"Created notification provider: {name} ({provider_type})")
        return jsonify(provider.to_dict(include_config=True)), 201

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating notification provider: {e}")
        return jsonify({'error': 'Failed to create provider'}), 500


@notification_bp.route('/providers/<int:provider_id>', methods=['PUT'])
@auth_required
def update_provider(provider_id):
    """Update a notification provider

    Args:
        provider_id: The provider ID

    Request body:
        - name: Provider name (optional)
        - configuration: Provider-specific config (optional)
        - is_active: Whether provider is active (optional)

    Returns:
        JSON updated provider
    """
    try:
        provider = db.session.get(NotificationProvider, provider_id)
        if not provider:
            return jsonify({'error': 'Provider not found'}), 404

        data = request.get_json()
        if not data:
            return jsonify({'error': 'Request body is required'}), 400

        # Update fields if provided
        if 'name' in data:
            provider.name = data['name']

        if 'configuration' in data:
            configuration = _preserve_masked_secrets(provider.configuration, data['configuration'])
            validation_error = _validate_provider_config(provider.provider_type, configuration)
            if validation_error:
                return jsonify({'error': validation_error}), 400
            provider.configuration = configuration

        if 'is_active' in data:
            provider.is_active = data['is_active']

        db.session.commit()

        logger.info(f"Updated notification provider: {provider.name}")
        return jsonify(provider.to_dict(include_config=True)), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating provider {provider_id}: {e}")
        return jsonify({'error': 'Failed to update provider'}), 500


@notification_bp.route('/providers/<int:provider_id>', methods=['DELETE'])
@auth_required
def delete_provider(provider_id):
    """Delete a notification provider

    Args:
        provider_id: The provider ID

    Returns:
        Empty response with 204 status
    """
    try:
        provider = db.session.get(NotificationProvider, provider_id)
        if not provider:
            return jsonify({'error': 'Provider not found'}), 404

        provider_name = provider.name
        db.session.delete(provider)
        db.session.commit()

        logger.info(f"Deleted notification provider: {provider_name}")
        return '', 204

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting provider {provider_id}: {e}")
        return jsonify({'error': 'Failed to delete provider'}), 500


@notification_bp.route('/providers/<int:provider_id>/test', methods=['POST'])
@auth_required
def test_provider(provider_id):
    """Test a notification provider by sending a test notification

    Args:
        provider_id: The provider ID

    Returns:
        JSON with test result
    """
    try:
        provider = db.session.get(NotificationProvider, provider_id)
        if not provider:
            return jsonify({'error': 'Provider not found'}), 404

        service = NotificationService()
        success, error = service.test_provider(
            provider_type=provider.provider_type,
            provider_config=provider.configuration
        )

        # Update provider status
        provider.last_notification_status = 'success' if success else 'failure'
        provider.last_notification_time = datetime.now(timezone.utc)
        db.session.commit()

        if success:
            return jsonify({'success': True, 'message': 'Test notification sent successfully'}), 200
        logger.warning("Notification provider %s test failed: %s", provider_id, error)
        return jsonify({'success': False, 'error': 'Test notification failed'}), 400

    except Exception as e:
        logger.error(f"Error testing provider {provider_id}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to test provider'}), 500


# ==================== Rule Endpoints ====================

@notification_bp.route('/rules', methods=['GET'])
@auth_required
def get_rules():
    """Get all notification rules

    Returns:
        JSON list of all notification rules
    """
    try:
        rules = NotificationRule.query.all()
        return jsonify([r.to_dict() for r in rules]), 200
    except Exception as e:
        logger.error(f"Error getting notification rules: {e}")
        return jsonify({'error': 'Failed to get notification rules'}), 500


@notification_bp.route('/rules/<int:rule_id>', methods=['GET'])
@auth_required
def get_rule(rule_id):
    """Get a specific notification rule

    Args:
        rule_id: The rule ID

    Returns:
        JSON rule details
    """
    try:
        rule = db.session.get(NotificationRule, rule_id)
        if not rule:
            return jsonify({'error': 'Rule not found'}), 404
        return jsonify(rule.to_dict()), 200
    except Exception as e:
        logger.error(f"Error getting rule {rule_id}: {e}")
        return jsonify({'error': 'Failed to get rule'}), 500


@notification_bp.route('/rules', methods=['POST'])
@auth_required
def create_rule():
    """Create a new notification rule

    Request body:
        - provider_id: Provider ID (required)
        - event_type: Event type (required)
        - is_active: Whether rule is active (optional, default: true)
        - priority: Priority level (optional, default: 'normal')
        - conditions: Optional conditions (optional)

    Returns:
        JSON created rule
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Request body is required'}), 400

        # Validate required fields
        provider_id = data.get('provider_id')
        event_type = data.get('event_type')

        if not provider_id:
            return jsonify({'error': 'Provider ID is required'}), 400
        if not event_type:
            return jsonify({'error': 'Event type is required'}), 400
        if event_type not in VALID_EVENT_TYPES:
            return jsonify({'error': f'Invalid event type. Must be one of: {VALID_EVENT_TYPES}'}), 400

        # Verify provider exists
        provider = db.session.get(NotificationProvider, provider_id)
        if not provider:
            return jsonify({'error': 'Provider not found'}), 404

        # Validate priority
        priority = data.get('priority', 'normal')
        if priority not in VALID_PRIORITIES:
            return jsonify({'error': f'Invalid priority. Must be one of: {VALID_PRIORITIES}'}), 400

        rule = NotificationRule(
            provider_id=provider_id,
            event_type=event_type,
            is_active=data.get('is_active', True),
            priority=priority,
            conditions=data.get('conditions')
        )

        db.session.add(rule)
        db.session.commit()

        logger.info(f"Created notification rule: {event_type} for provider {provider.name}")
        return jsonify(rule.to_dict()), 201

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating notification rule: {e}")
        return jsonify({'error': 'Failed to create rule'}), 500


@notification_bp.route('/rules/<int:rule_id>', methods=['PUT'])
@auth_required
def update_rule(rule_id):
    """Update a notification rule

    Args:
        rule_id: The rule ID

    Request body:
        - event_type: Event type (optional)
        - is_active: Whether rule is active (optional)
        - priority: Priority level (optional)
        - conditions: Optional conditions (optional)

    Returns:
        JSON updated rule
    """
    try:
        rule = db.session.get(NotificationRule, rule_id)
        if not rule:
            return jsonify({'error': 'Rule not found'}), 404

        data = request.get_json()
        if not data:
            return jsonify({'error': 'Request body is required'}), 400

        # Update fields if provided
        if 'event_type' in data:
            if data['event_type'] not in VALID_EVENT_TYPES:
                return jsonify({'error': f'Invalid event type. Must be one of: {VALID_EVENT_TYPES}'}), 400
            rule.event_type = data['event_type']

        if 'is_active' in data:
            rule.is_active = data['is_active']

        if 'priority' in data:
            if data['priority'] not in VALID_PRIORITIES:
                return jsonify({'error': f'Invalid priority. Must be one of: {VALID_PRIORITIES}'}), 400
            rule.priority = data['priority']

        if 'conditions' in data:
            rule.conditions = data['conditions']

        db.session.commit()

        logger.info(f"Updated notification rule {rule_id}")
        return jsonify(rule.to_dict()), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating rule {rule_id}: {e}")
        return jsonify({'error': 'Failed to update rule'}), 500


@notification_bp.route('/rules/<int:rule_id>', methods=['DELETE'])
@auth_required
def delete_rule(rule_id):
    """Delete a notification rule

    Args:
        rule_id: The rule ID

    Returns:
        Empty response with 204 status
    """
    try:
        rule = db.session.get(NotificationRule, rule_id)
        if not rule:
            return jsonify({'error': 'Rule not found'}), 404

        db.session.delete(rule)
        db.session.commit()

        logger.info(f"Deleted notification rule {rule_id}")
        return '', 204

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting rule {rule_id}: {e}")
        return jsonify({'error': 'Failed to delete rule'}), 500


# ==================== Helper Functions ====================

def _preserve_masked_secrets(stored: dict, incoming: dict) -> dict:
    """Keep the stored secret when the caller sends the mask back unchanged.

    to_dict(include_config=True) returns secrets as '***', so a client that GETs
    a provider, edits one field and PUTs the whole object back would otherwise
    overwrite the real credential with the literal mask and silently break
    delivery. Applies to every provider type, not just email.
    """
    merged = dict(incoming or {})
    for key, value in merged.items():
        if value == '***' and stored and key in stored:
            merged[key] = stored[key]
    return merged


def _validate_provider_config(provider_type: str, config: dict) -> Optional[str]:
    """Validate provider-specific configuration

    Args:
        provider_type: Type of provider
        config: Configuration dictionary

    Returns:
        Error message if validation fails, None otherwise
    """
    if provider_type == 'pushover':
        if not config.get('api_token'):
            return 'Pushover API token is required'
        if not config.get('user_key'):
            return 'Pushover user key is required'

    elif provider_type == 'ntfy':
        if not config.get('topic'):
            return 'ntfy topic is required'
        # Support both 'server_url' (canonical) and 'server' (legacy) field names
        server_url = config.get('server_url') or config.get('server')
        if not server_url:
            return 'ntfy server URL is required'
        # SSRF protection: validate server URL
        is_safe, error = validate_safe_url(f"{server_url}/{config.get('topic')}")
        if not is_safe:
            return f'ntfy server URL blocked by security policy: {error}'

    elif provider_type == 'webhook':
        if not config.get('webhook_url'):
            return 'Webhook URL is required'
        # SSRF protection: validate webhook URL
        is_safe, error = validate_safe_url(config['webhook_url'])
        if not is_safe:
            return f'Webhook URL blocked by security policy: {error}'

    elif provider_type == 'email':
        smtp_host = (config.get('smtp_host') or '').strip()
        if not smtp_host:
            return 'SMTP host is required'
        if not (config.get('from_address') or '').strip():
            return 'From address is required'
        if not parse_recipients(config.get('recipients')):
            return 'At least one recipient is required'

        security = (config.get('security') or 'starttls').lower()
        if security not in VALID_EMAIL_SECURITY:
            return f'Invalid security mode. Must be one of: {list(VALID_EMAIL_SECURITY)}'

        try:
            port = resolve_smtp_port(config, security)
        except (TypeError, ValueError):
            return 'SMTP port must be a number'
        if not 1 <= port <= 65535:
            return 'SMTP port must be between 1 and 65535'

        # SSRF protection: unlike webhooks, private/LAN relays stay allowed --
        # only cloud metadata and link-local targets are refused.
        is_safe, error = validate_outbound_host(smtp_host, port)
        if not is_safe:
            return f'SMTP host blocked by security policy: {error}'

    return None

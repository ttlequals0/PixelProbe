"""
Notification service for PixelProbe

Supports multiple notification providers:
- Pushover API
- ntfy.sh API
- Generic webhooks
- Email (SMTP)
"""

import logging
import re
import smtplib
import ssl
import requests
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from urllib.parse import urlparse
from typing import Dict, Optional, List
from datetime import datetime, timezone
from pixelprobe.models import (db, HealthcheckConfig, NotificationRule,
                               ScanNotificationDelivery, ScanReport)
from pixelprobe.utils.security import validate_safe_url, create_safe_session, validate_outbound_host

logger = logging.getLogger(__name__)

VALID_EMAIL_SECURITY = ('starttls', 'ssl', 'none')

# Event types are a contract: each item must have a production dispatch site.
# Lifecycle code extends this mapping when it adds the matching outbox event.
SUPPORTED_NOTIFICATION_EVENTS = frozenset({'bitrot_suspected', 'scan_completed'})

# Public rule capability contract. Conditions may only refer to values emitted
# by the production dispatch sites, never to arbitrary keys that would remain
# silently false forever.
NOTIFICATION_EVENT_CONDITIONS = {
    'scan_completed': {
        'scan_id': 'string',
        'status': 'string',
        'files_scanned': 'number',
        'corrupted_count': 'number',
        'warning_count': 'number',
        'error_count': 'number',
    },
    'bitrot_suspected': {'count': 'number'},
}


def conditions_match(conditions, event_data):
    """Evaluate all configured numeric/string comparisons against event data."""
    if not conditions:
        return True
    for key, expression in conditions.items():
        actual = event_data.get(key)
        if actual is None:
            return False
        if isinstance(expression, dict):
            operator, expected = expression.get('operator'), expression.get('value')
        else:
            operator, expected = 'eq', expression
            if isinstance(expression, str):
                for prefix, comparison in (('>=', 'gte'), ('<=', 'lte'),
                                           ('>', 'gt'), ('<', 'lt')):
                    if expression.startswith(prefix):
                        operator = comparison
                        try:
                            expected = float(expression[len(prefix):].strip())
                        except ValueError:
                            return False
                        break
        if operator == 'eq' and actual != expected:
            return False
        if operator in {'gt', 'gte', 'lt', 'lte'}:
            if not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
                return False
            if operator == 'gt' and not actual > expected:
                return False
            if operator == 'gte' and not actual >= expected:
                return False
            if operator == 'lt' and not actual < expected:
                return False
            if operator == 'lte' and not actual <= expected:
                return False
        if operator not in {'eq', 'gt', 'gte', 'lt', 'lte'}:
            return False
    return True


def parse_recipients(raw) -> List[str]:
    """Normalize recipients from a list or a comma-separated string."""
    if isinstance(raw, (list, tuple)):
        values = raw
    else:
        values = (raw or '').split(',')
    return [str(v).strip() for v in values if str(v).strip()]


def resolve_smtp_port(config: Dict, security: str) -> int:
    """Configured SMTP port, or the conventional default for the security mode.

    Shared so the API validator and the sender cannot disagree about which port
    a provider will actually use. Raises ValueError on a non-numeric port.
    """
    port = config.get('smtp_port')
    if port in (None, ''):
        return 465 if security == 'ssl' else 587
    return int(port)


def snapshot_event_targets(event_type, priority='normal', additional_data=None):
    """Capture every rule's eligibility and provider configuration for one event.

    The result is intentionally plain data so a durable outbox can retain the
    exact targets selected at finalization time. Later rule or provider edits
    must not turn a failed delivery into a new destination.
    """
    event_data = additional_data or {}
    rules = NotificationRule.query.filter_by(event_type=event_type).all()
    targets = []
    for rule in rules:
        provider = rule.provider
        target = {
            'rule_id': rule.id,
            'provider_id': provider.id if provider else rule.provider_id,
            'provider_type': provider.provider_type if provider else None,
            'provider_config': dict(provider.configuration or {}) if provider else None,
            'conditions': dict(rule.conditions or {}),
            'priority': (rule.priority if rule.priority and rule.priority != 'normal'
                         else priority),
            'eligible': True,
            'skip_reason': None,
        }
        if not rule.is_active:
            target.update(eligible=False, skip_reason='rule_inactive')
        elif not provider or not provider.is_active:
            target.update(eligible=False, skip_reason='provider_inactive')
        elif not conditions_match(rule.conditions, event_data):
            target.update(eligible=False, skip_reason='conditions_not_met')
        targets.append(target)
    return targets


def snapshot_scan_notification_outbox(outbox, report=None):
    """Persist an outbox event's immutable payload and evaluated rule targets.

    This helper is Celery-free so scan finalization can call it before the
    transaction that commits the report and outbox row. It is idempotent for
    legacy recovery rows that have not yet been initialized.
    """
    if outbox.targets_initialized:
        return
    if report is None:
        report = (ScanReport.query.filter_by(scan_id=outbox.scan_id)
                  .order_by(ScanReport.id.desc()).first())
    succeeded = not report or report.status == 'completed'
    payload = outbox.payload or {
        'title': ('PixelProbe scan completed' if succeeded
                  else 'PixelProbe scan finished with errors'),
        'message': (f'Scan {outbox.scan_id} completed.' if succeeded
                    else f'Scan {outbox.scan_id} finished with errors.'),
        'additional_data': {
            'scan_id': outbox.scan_id,
            'status': report.status if report else None,
            'files_scanned': report.files_scanned if report else 0,
            'corrupted_count': report.files_corrupted if report else 0,
            'warning_count': report.files_with_warnings if report else 0,
            'error_count': report.files_error if report else 0,
        },
    }
    outbox.payload = payload
    for target in snapshot_event_targets(outbox.event, additional_data=payload['additional_data']):
        eligible = target['eligible']
        db.session.add(ScanNotificationDelivery(
            outbox_id=outbox.id,
            rule_id=target['rule_id'],
            provider_id=target['provider_id'],
            provider_type=target['provider_type'],
            provider_config=target['provider_config'],
            conditions=target['conditions'],
            priority=target['priority'],
            status='pending' if eligible else 'skipped',
            outcome=None if eligible else 'skipped',
            skip_reason=target['skip_reason'],
        ))
    snapshot_scheduled_healthcheck_target(outbox, report)
    outbox.targets_initialized = True


def _scheduled_scan_id(scan_id):
    match = re.match(r'^scheduled_(\d+)(?:_|$)', scan_id or '')
    return int(match.group(1)) if match else None


def snapshot_scheduled_healthcheck_target(outbox, report):
    """Persist the completion healthcheck target selected for this scan."""
    schedule_id = _scheduled_scan_id(outbox.scan_id)
    if not schedule_id or not report:
        return
    config = HealthcheckConfig.query.filter_by(schedule_id=schedule_id).first()
    if not config:
        return

    mode = 'success' if report.status == 'completed' else 'failure'
    enabled = config.is_active and (
        config.send_success_ping if mode == 'success' else config.send_failure_ping)
    if not config.is_active:
        skip_reason = 'healthcheck_inactive'
    elif not enabled:
        skip_reason = f'healthcheck_{mode}_disabled'
    else:
        skip_reason = None
    report_data = report.to_dict() if config.include_report_data else None
    db.session.add(ScanNotificationDelivery(
        outbox_id=outbox.id,
        provider_type='healthcheck',
        provider_config={
            'healthcheck_config_id': config.id,
            'healthcheck_url': config.healthcheck_url,
            'mode': mode,
            'include_report_data': bool(config.include_report_data),
            'report_data': report_data,
            'error_message': (report.error_message or f'Scan status: {report.status}'),
        },
        priority='normal',
        status='pending' if enabled else 'skipped',
        outcome=None if enabled else 'skipped',
        skip_reason=skip_reason,
    ))


def deliver_healthcheck_target(snapshot):
    """Deliver one immutable scheduled-healthcheck completion target."""
    from pixelprobe.services.healthcheck_service import HealthcheckService

    mode = snapshot.get('mode')
    url = snapshot.get('healthcheck_url')
    if mode == 'success':
        success = HealthcheckService().ping_success(
            url, snapshot.get('report_data') if snapshot.get('include_report_data') else None)
    elif mode == 'failure':
        success = HealthcheckService().ping_fail(url, snapshot.get('error_message'))
    else:
        return False, 'invalid healthcheck target mode'
    return (True, None) if success else (False, 'healthcheck ping failed')


def deliver_notification_target(provider_type, provider_config, title, message,
                                priority='normal', additional_data=None):
    """Deliver one already-selected target and return its provider outcome.

    Callers own persistence and retries. A provider exception is converted to
    a failed outcome so the generic dispatcher remains best-effort and the
    scan outbox can retry only that target.
    """
    try:
        return NotificationService().send_notification(
            provider_type=provider_type,
            provider_config=provider_config,
            title=title,
            message=message,
            priority=priority,
            additional_data=additional_data,
        )
    except Exception as exc:
        logger.error("Notification delivery raised for provider type %s", provider_type)
        return False, str(exc)


def dispatch_event(event_type, title, message, priority='normal', additional_data=None):
    """Send an event through every active NotificationRule for event_type.

    This is the rule-evaluation layer the CRUD-only rules previously lacked:
    it joins active rules to their active providers and delivers via
    NotificationService, recording per-provider delivery status. Returns the
    number of successful deliveries. Failures are logged, never raised -
    notification must not break the operation that triggered it.
    """
    try:
        targets = snapshot_event_targets(event_type, priority, additional_data)
    except Exception as e:
        logger.error("Could not load notification rules")
        return 0

    if not targets:
        logger.debug(f"No active notification rules for event {event_type}")
        return 0

    sent = 0
    for target in targets:
        if not target['eligible']:
            continue
        try:
            success, error = deliver_notification_target(
                provider_type=target['provider_type'],
                provider_config=target['provider_config'],
                title=title,
                message=message,
                priority=target['priority'],
                additional_data=additional_data,
            )
            rule = db.session.get(NotificationRule, target['rule_id'])
            provider = rule.provider if rule else None
            if not provider:
                continue
            provider.last_notification_status = 'success' if success else 'failure'
            provider.last_notification_time = datetime.now(timezone.utc)
            if success:
                sent += 1
            else:
                logger.warning("Notification delivery failed for provider %s", provider.id)
        except Exception as e:
            logger.error("Notification delivery bookkeeping raised for provider %s",
                         target['provider_id'])

    try:
        db.session.commit()
    except Exception as e:
        logger.error(f"Could not record notification delivery status: {e}")
        db.session.rollback()

    return sent


class NotificationService:
    """Service for sending notifications via various providers"""

    def __init__(self):
        self.session = create_safe_session()
        self.session.headers.update({'User-Agent': 'PixelProbe-Notification/1.0'})
        self.timeout = 10

    def send_notification(
        self,
        provider_type: str,
        provider_config: Dict,
        title: str,
        message: str,
        priority: str = 'normal',
        additional_data: Optional[Dict] = None
    ) -> tuple[bool, Optional[str]]:
        """
        Send a notification via the specified provider

        Args:
            provider_type: Type of provider ('pushover', 'ntfy', 'webhook', 'email')
            provider_config: Provider-specific configuration
            title: Notification title
            message: Notification message
            priority: Priority level ('low', 'normal', 'high')
            additional_data: Additional data to include

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        try:
            if provider_type == 'pushover':
                return self._send_pushover(provider_config, title, message, priority, additional_data)
            elif provider_type == 'ntfy':
                return self._send_ntfy(provider_config, title, message, priority, additional_data)
            elif provider_type == 'webhook':
                return self._send_webhook(provider_config, title, message, priority, additional_data)
            elif provider_type == 'email':
                return self._send_email(provider_config, title, message, priority, additional_data)
            else:
                return False, f"Unknown provider type: {provider_type}"

        except Exception as e:
            logger.error("Error sending notification via provider type %s", provider_type)
            return False, str(e)

    def _send_pushover(
        self,
        config: Dict,
        title: str,
        message: str,
        priority: str,
        additional_data: Optional[Dict]
    ) -> tuple[bool, Optional[str]]:
        """
        Send notification via Pushover API

        Config should contain:
            - api_token: Pushover API token
            - user_key: Pushover user/group key
            - device: Optional device name
        """
        api_token = config.get('api_token')
        user_key = config.get('user_key')

        if not api_token or not user_key:
            return False, "Missing required Pushover credentials (api_token and user_key)"

        # Map priority levels
        priority_map = {
            'low': -1,
            'normal': 0,
            'high': 1
        }

        payload = {
            'token': api_token,
            'user': user_key,
            'title': title,
            'message': message,
            'priority': priority_map.get(priority, 0),
            'timestamp': int(datetime.now(timezone.utc).timestamp())
        }

        # Add optional device
        if config.get('device'):
            payload['device'] = config['device']

        # Add sound if configured
        if config.get('sound'):
            payload['sound'] = config['sound']

        try:
            response = self.session.post(
                'https://api.pushover.net/1/messages.json',
                data=payload,
                timeout=self.timeout
            )

            if response.status_code == 200:
                result = response.json()
                if result.get('status') == 1:
                    logger.info(f"Pushover notification sent successfully")
                    return True, None
                else:
                    errors = result.get('errors', ['Unknown error'])
                    error_msg = ', '.join(errors)
                    logger.warning(f"Pushover API returned error: {error_msg}")
                    return False, error_msg
            else:
                logger.warning(f"Pushover API returned status {response.status_code}")
                return False, f"HTTP {response.status_code}"

        except requests.RequestException as e:
            logger.error("Pushover request failed")
            return False, str(e)

    def _send_ntfy(
        self,
        config: Dict,
        title: str,
        message: str,
        priority: str,
        additional_data: Optional[Dict]
    ) -> tuple[bool, Optional[str]]:
        """
        Send notification via ntfy.sh API

        Config should contain:
            - server_url: ntfy server URL (default: https://ntfy.sh)
            - topic: ntfy topic name
            - token: Optional authentication token
        """
        server_url = config.get('server_url', 'https://ntfy.sh').rstrip('/')
        topic = config.get('topic')

        if not topic:
            return False, "Missing required ntfy topic"

        # Map priority levels
        priority_map = {
            'low': 2,
            'normal': 3,
            'high': 4
        }

        headers = {
            'Title': title,
            'Priority': str(priority_map.get(priority, 3)),
            'Tags': 'pixelprobe'
        }

        # Add authentication token if provided
        token = config.get('token')
        if token:
            headers['Authorization'] = f'Bearer {token}'

        # SSRF protection: validate URL before making request
        ntfy_url = f"{server_url}/{topic}"
        is_safe, error = validate_safe_url(ntfy_url)
        if not is_safe:
            return False, f"ntfy URL blocked by security policy: {error}"

        try:
            response = self.session.post(
                f"{server_url}/{topic}",
                data=message.encode('utf-8'),
                headers=headers,
                timeout=self.timeout
            )

            if response.status_code == 200:
                logger.info("ntfy notification sent successfully")
                return True, None
            else:
                logger.warning(f"ntfy API returned status {response.status_code}")
                try:
                    error_detail = response.json().get('error', response.text)
                except:
                    error_detail = response.text
                return False, f"HTTP {response.status_code}: {error_detail}"

        except requests.RequestException as e:
            logger.error("ntfy request failed")
            return False, str(e)

    def _send_webhook(
        self,
        config: Dict,
        title: str,
        message: str,
        priority: str,
        additional_data: Optional[Dict]
    ) -> tuple[bool, Optional[str]]:
        """
        Send notification via generic webhook

        Config should contain:
            - webhook_url: Webhook URL
            - method: HTTP method (default: POST)
            - headers: Optional custom headers dict
            - template: Optional template type ('slack', 'discord', 'custom')
        """
        webhook_url = config.get('webhook_url')

        if not webhook_url:
            return False, "Missing required webhook_url"

        method = config.get('method', 'POST').upper()
        custom_headers = config.get('headers', {})
        template = config.get('template', 'custom')

        # Build payload based on template
        if template == 'slack':
            payload = {
                'text': title,
                'blocks': [
                    {
                        'type': 'header',
                        'text': {
                            'type': 'plain_text',
                            'text': title
                        }
                    },
                    {
                        'type': 'section',
                        'text': {
                            'type': 'mrkdwn',
                            'text': message
                        }
                    }
                ]
            }
            if additional_data:
                payload['blocks'].append({
                    'type': 'context',
                    'elements': [
                        {
                            'type': 'mrkdwn',
                            'text': f"Priority: *{priority}*"
                        }
                    ]
                })

        elif template == 'discord':
            # Discord webhook format
            color_map = {
                'low': 0x808080,      # Gray
                'normal': 0x007bff,   # Blue
                'high': 0xdc3545      # Red
            }
            payload = {
                'embeds': [{
                    'title': title,
                    'description': message,
                    'color': color_map.get(priority, 0x007bff),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }]
            }
            if additional_data:
                payload['embeds'][0]['fields'] = [
                    {'name': k, 'value': str(v), 'inline': True}
                    for k, v in additional_data.items()
                ]

        else:
            # Custom/generic payload
            payload = {
                'title': title,
                'message': message,
                'priority': priority,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'source': 'PixelProbe'
            }
            if additional_data:
                payload['data'] = additional_data

        headers = {'Content-Type': 'application/json'}
        headers.update(custom_headers)

        # SSRF protection: validate URL before making request
        is_safe, error = validate_safe_url(webhook_url)
        if not is_safe:
            return False, f"Webhook URL blocked by security policy: {error}"

        try:
            if method == 'POST':
                response = self.session.post(
                    webhook_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout
                )
            elif method == 'PUT':
                response = self.session.put(
                    webhook_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout
                )
            else:
                return False, f"Unsupported HTTP method: {method}"

            # Accept 2xx status codes as success
            if 200 <= response.status_code < 300:
                # Host only: Slack and Discord webhook URLs are bearer secrets in
                # the path, so the full URL must not reach the logs.
                logger.info(
                    f"Webhook notification sent successfully to "
                    f"{urlparse(webhook_url).netloc or 'configured endpoint'}"
                )
                return True, None
            else:
                logger.warning(f"Webhook returned status {response.status_code}")
                return False, f"HTTP {response.status_code}"

        except requests.RequestException as e:
            logger.error("Webhook request failed")
            return False, str(e)

    def _send_email(
        self,
        config: Dict,
        title: str,
        message: str,
        priority: str,
        additional_data: Optional[Dict]
    ) -> tuple[bool, Optional[str]]:
        """
        Send notification via SMTP

        Config should contain:
            - smtp_host: SMTP server hostname (required)
            - smtp_port: SMTP port (default: 465 when security is 'ssl', else 587)
            - security: 'starttls' (default), 'ssl', or 'none'
            - username: Optional SMTP username
            - password: Optional SMTP password
            - from_address: Envelope/From address (required)
            - recipients: List of addresses, or a comma-separated string (required)

        One connection per send, under a hard timeout, so a hung mail server
        cannot stall the caller (dispatch_event runs inside scan paths).
        """
        smtp_host = (config.get('smtp_host') or '').strip()
        from_address = (config.get('from_address') or '').strip()
        recipients = parse_recipients(config.get('recipients'))

        if not smtp_host:
            return False, "Missing required smtp_host"
        if not from_address:
            return False, "Missing required from_address"
        if not recipients:
            return False, "At least one recipient is required"

        security = (config.get('security') or 'starttls').lower()
        if security not in VALID_EMAIL_SECURITY:
            return False, f"Invalid security mode '{security}'. Must be one of: {list(VALID_EMAIL_SECURITY)}"

        try:
            smtp_port = resolve_smtp_port(config, security)
        except (TypeError, ValueError):
            return False, "smtp_port must be a number"

        # Re-validated here rather than only on save, so a DNS rebind cannot
        # repoint a stored hostname at a blocked target between the two.
        is_safe, error = validate_outbound_host(smtp_host, smtp_port)
        if not is_safe:
            return False, f"SMTP host blocked by security policy: {error}"

        msg = EmailMessage()
        msg['Subject'] = f"[PixelProbe] {title}"
        msg['From'] = from_address
        msg['To'] = ', '.join(recipients)
        # Date is mandatory per RFC 5322 and smtplib does not add one; without it
        # spam filters penalise the message and strict MTAs may reject it.
        # Message-ID is derived from the sender domain rather than make_msgid's
        # default, which would otherwise leak the container hostname.
        msg['Date'] = formatdate(localtime=True)
        sender_domain = from_address.rpartition('@')[2].strip('>') or None
        msg['Message-ID'] = make_msgid(domain=sender_domain)
        msg.set_content(self._render_email_body(message, priority, additional_data))

        try:
            context = ssl.create_default_context()
            if security == 'ssl':
                smtp = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=self.timeout, context=context)
            else:
                smtp = smtplib.SMTP(smtp_host, smtp_port, timeout=self.timeout)

            with smtp:
                if security == 'starttls':
                    smtp.starttls(context=context)
                    smtp.ehlo()
                username = config.get('username')
                password = config.get('password')
                if username and password:
                    smtp.login(username, password)
                elif username:
                    logger.warning("SMTP username set without a password; connecting without login")
                smtp.send_message(msg)

            logger.info("Email notification sent to %s recipient(s)", len(recipients))
            return True, None

        except (smtplib.SMTPException, ssl.SSLError, OSError) as e:
            logger.error("Email send failed")
            return False, str(e)

    def _render_email_body(
        self,
        message: str,
        priority: str,
        additional_data: Optional[Dict]
    ) -> str:
        """Build the plain-text email body in a 'Label: value' style."""
        lines = [message, '']
        lines.append(f"Priority: {priority}")
        if additional_data:
            for key, value in additional_data.items():
                label = key.replace('_', ' ').capitalize()
                lines.append(f"{label}: {'-' if value is None or value == '' else value}")
        lines.append('')
        lines.append('Sent by PixelProbe.')
        return '\n'.join(lines)

    def test_provider(
        self,
        provider_type: str,
        provider_config: Dict
    ) -> tuple[bool, Optional[str]]:
        """
        Test a notification provider configuration

        Args:
            provider_type: Type of provider
            provider_config: Provider configuration

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        test_title = "PixelProbe Test Notification"
        test_message = "PixelProbe test notification. Receiving it confirms this provider is configured."

        return self.send_notification(
            provider_type=provider_type,
            provider_config=provider_config,
            title=test_title,
            message=test_message,
            priority='normal'
        )

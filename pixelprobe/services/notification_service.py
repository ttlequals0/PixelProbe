"""
Notification service for PixelProbe

Supports multiple notification providers:
- Pushover API
- ntfy.sh API
- Generic webhooks
- Email (SMTP)
"""

import logging
import smtplib
import ssl
import requests
from email.message import EmailMessage
from typing import Dict, Optional, List
from datetime import datetime, timezone
from pixelprobe.models import db, NotificationRule
from pixelprobe.utils.security import validate_safe_url, create_safe_session, validate_outbound_host

logger = logging.getLogger(__name__)

VALID_EMAIL_SECURITY = ('starttls', 'ssl', 'none')


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


def dispatch_event(event_type, title, message, priority='normal', additional_data=None):
    """Send an event through every active NotificationRule for event_type.

    This is the rule-evaluation layer the CRUD-only rules previously lacked:
    it joins active rules to their active providers and delivers via
    NotificationService, recording per-provider delivery status. Returns the
    number of successful deliveries. Failures are logged, never raised -
    notification must not break the operation that triggered it.
    """
    try:
        rules = NotificationRule.query.filter_by(event_type=event_type, is_active=True).all()
    except Exception as e:
        logger.error(f"Could not load notification rules for {event_type}: {e}")
        return 0

    if not rules:
        logger.debug(f"No active notification rules for event {event_type}")
        return 0

    service = NotificationService()
    sent = 0
    for rule in rules:
        provider = rule.provider
        if not provider or not provider.is_active:
            continue
        try:
            success, error = service.send_notification(
                provider_type=provider.provider_type,
                provider_config=provider.configuration,
                title=title,
                message=message,
                # rule.priority defaults to 'normal' (NOT NULL), so it cannot
                # simply win over the event: an explicitly raised/lowered rule
                # priority applies, otherwise the event's priority does (e.g.
                # bitrot dispatches at 'high')
                priority=rule.priority if rule.priority and rule.priority != 'normal' else priority,
                additional_data=additional_data
            )
            provider.last_notification_status = 'success' if success else 'failure'
            provider.last_notification_time = datetime.now(timezone.utc)
            if success:
                sent += 1
            else:
                logger.warning(f"Notification via {provider.name} failed for {event_type}: {error}")
        except Exception as e:
            logger.error(f"Notification via provider {provider.id} raised for {event_type}: {e}")

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
            logger.error(f"Error sending notification via {provider_type}: {e}")
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
            logger.error(f"Pushover request failed: {e}")
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
                logger.info(f"ntfy notification sent successfully to {topic}")
                return True, None
            else:
                logger.warning(f"ntfy API returned status {response.status_code}")
                try:
                    error_detail = response.json().get('error', response.text)
                except:
                    error_detail = response.text
                return False, f"HTTP {response.status_code}: {error_detail}"

        except requests.RequestException as e:
            logger.error(f"ntfy request failed: {e}")
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
                logger.info(f"Webhook notification sent successfully to {webhook_url}")
                return True, None
            else:
                logger.warning(f"Webhook returned status {response.status_code}")
                return False, f"HTTP {response.status_code}"

        except requests.RequestException as e:
            logger.error(f"Webhook request failed: {e}")
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

            logger.info(f"Email notification sent to {len(recipients)} recipient(s) via {smtp_host}")
            return True, None

        except (smtplib.SMTPException, ssl.SSLError, OSError) as e:
            logger.error(f"Email send failed via {smtp_host}: {e}")
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
        test_message = "This is a test notification from PixelProbe. If you receive this, your notification provider is configured correctly."

        return self.send_notification(
            provider_type=provider_type,
            provider_config=provider_config,
            title=test_title,
            message=test_message,
            priority='normal'
        )

"""
Notification service for PixelProbe

Supports multiple notification providers:
- Pushover API
- ntfy.sh API
- Generic webhooks
"""

import logging
import requests
from typing import Dict, Optional, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending notifications via various providers"""

    def __init__(self):
        self.session = requests.Session()
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
            provider_type: Type of provider ('pushover', 'ntfy', 'webhook')
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

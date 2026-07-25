"""Unit tests for the SMTP email notification provider"""

import smtplib
from unittest.mock import MagicMock, patch

import pytest

from pixelprobe.api.notification_routes import (
    VALID_PROVIDER_TYPES, _preserve_masked_secrets, _validate_provider_config
)
from pixelprobe.services.notification_service import NotificationService, parse_recipients
from pixelprobe.utils.security import validate_outbound_host

BASE_CONFIG = {
    'smtp_host': 'smtp.example.com',
    'smtp_port': 587,
    'security': 'starttls',
    'username': 'probe@example.com',
    'password': 'secret',
    'from_address': 'probe@example.com',
    'recipients': ['ops@example.com', 'admin@example.com'],
}


class TestParseRecipients:

    def test_list_is_stripped(self):
        assert parse_recipients([' a@b.com ', 'c@d.com']) == ['a@b.com', 'c@d.com']

    def test_comma_string_is_split(self):
        assert parse_recipients('a@b.com, c@d.com') == ['a@b.com', 'c@d.com']

    def test_empty_values_dropped(self):
        assert parse_recipients('a@b.com,,  ,') == ['a@b.com']

    def test_none_is_empty(self):
        assert parse_recipients(None) == []


class TestValidateOutboundHost:
    """SMTP relays legitimately live on the LAN, unlike webhook targets"""

    def test_private_host_allowed(self):
        with patch('socket.getaddrinfo', return_value=[
            (2, 1, 6, '', ('192.168.1.10', 25))
        ]):
            assert validate_outbound_host('mail.lan', 25) == (True, None)

    def test_loopback_allowed(self):
        with patch('socket.getaddrinfo', return_value=[
            (2, 1, 6, '', ('127.0.0.1', 25))
        ]):
            assert validate_outbound_host('localhost', 25) == (True, None)

    def test_cloud_metadata_ip_blocked(self):
        is_safe, error = validate_outbound_host('169.254.169.254', 25)
        assert is_safe is False
        assert 'metadata' in error

    def test_hostname_resolving_to_metadata_blocked(self):
        with patch('socket.getaddrinfo', return_value=[
            (2, 1, 6, '', ('169.254.169.254', 25))
        ]):
            is_safe, error = validate_outbound_host('sneaky.example.com', 25)
        assert is_safe is False
        assert 'metadata' in error

    def test_link_local_blocked(self):
        with patch('socket.getaddrinfo', return_value=[
            (2, 1, 6, '', ('169.254.10.5', 25))
        ]):
            is_safe, error = validate_outbound_host('linklocal.example.com', 25)
        assert is_safe is False
        assert 'link-local' in error

    def test_unresolvable_host_allowed(self):
        import socket as socket_mod
        with patch('socket.getaddrinfo', side_effect=socket_mod.gaierror):
            assert validate_outbound_host('nope.invalid', 25) == (True, None)

    def test_empty_host_rejected(self):
        is_safe, error = validate_outbound_host('')
        assert is_safe is False
        assert 'empty' in error.lower()


class TestSendEmail:

    def _service(self):
        with patch('pixelprobe.services.notification_service.create_safe_session'):
            return NotificationService()

    @patch('pixelprobe.services.notification_service.validate_outbound_host',
           return_value=(True, None))
    @patch('smtplib.SMTP')
    def test_starttls_send(self, mock_smtp, _host):
        smtp = mock_smtp.return_value
        smtp.__enter__.return_value = smtp

        success, error = self._service()._send_email(
            BASE_CONFIG, 'Scan complete', '1200 files scanned', 'normal', None
        )

        assert (success, error) == (True, None)
        mock_smtp.assert_called_once()
        assert mock_smtp.call_args[0][0] == 'smtp.example.com'
        assert mock_smtp.call_args[0][1] == 587
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with('probe@example.com', 'secret')
        sent = smtp.send_message.call_args[0][0]
        assert sent['Subject'] == '[PixelProbe] Scan complete'
        assert sent['To'] == 'ops@example.com, admin@example.com'

    @patch('pixelprobe.services.notification_service.validate_outbound_host',
           return_value=(True, None))
    @patch('smtplib.SMTP_SSL')
    def test_ssl_send_uses_smtp_ssl(self, mock_smtp_ssl, _host):
        mock_smtp_ssl.return_value.__enter__.return_value = mock_smtp_ssl.return_value
        config = {**BASE_CONFIG, 'security': 'ssl', 'smtp_port': 465}

        success, error = self._service()._send_email(
            config, 'Test', 'body', 'normal', None
        )

        assert (success, error) == (True, None)
        assert mock_smtp_ssl.call_args[0][1] == 465

    @patch('pixelprobe.services.notification_service.validate_outbound_host',
           return_value=(True, None))
    @patch('smtplib.SMTP')
    def test_no_credentials_skips_login(self, mock_smtp, _host):
        smtp = mock_smtp.return_value
        smtp.__enter__.return_value = smtp
        config = {k: v for k, v in BASE_CONFIG.items() if k not in ('username', 'password')}

        success, _ = self._service()._send_email(config, 'Test', 'body', 'normal', None)

        assert success is True
        smtp.login.assert_not_called()

    @patch('pixelprobe.services.notification_service.validate_outbound_host',
           return_value=(True, None))
    @patch('smtplib.SMTP')
    def test_smtp_failure_returns_error_without_raising(self, mock_smtp, _host):
        mock_smtp.side_effect = smtplib.SMTPAuthenticationError(535, b'bad creds')

        success, error = self._service()._send_email(
            BASE_CONFIG, 'Test', 'body', 'normal', None
        )

        assert success is False
        assert error

    @patch('pixelprobe.services.notification_service.validate_outbound_host',
           return_value=(False, 'Host resolves to a link-local address (169.254.1.1)'))
    @patch('smtplib.SMTP')
    def test_blocked_host_never_connects(self, mock_smtp, _host):
        success, error = self._service()._send_email(
            BASE_CONFIG, 'Test', 'body', 'normal', None
        )

        assert success is False
        assert 'blocked by security policy' in error
        mock_smtp.assert_not_called()

    @pytest.mark.parametrize('missing', ['smtp_host', 'from_address', 'recipients'])
    def test_missing_required_field(self, missing):
        config = {k: v for k, v in BASE_CONFIG.items() if k != missing}
        success, error = self._service()._send_email(config, 'T', 'b', 'normal', None)
        assert success is False
        assert error

    def test_invalid_security_mode_rejected(self):
        config = {**BASE_CONFIG, 'security': 'tls-maybe'}
        success, error = self._service()._send_email(config, 'T', 'b', 'normal', None)
        assert success is False
        assert 'Invalid security mode' in error

    @patch('pixelprobe.services.notification_service.validate_outbound_host',
           return_value=(True, None))
    @patch('smtplib.SMTP')
    def test_additional_data_rendered_in_body(self, mock_smtp, _host):
        smtp = mock_smtp.return_value
        smtp.__enter__.return_value = smtp

        self._service()._send_email(
            BASE_CONFIG, 'Corruption found', 'A file failed validation', 'high',
            {'file_path': '/movies/x.mkv', 'corruption_details': None}
        )

        body = smtp.send_message.call_args[0][0].get_content()
        assert 'A file failed validation' in body
        assert 'Priority: high' in body
        assert 'File path: /movies/x.mkv' in body
        assert 'Corruption details: -' in body

    @patch('pixelprobe.services.notification_service.validate_outbound_host',
           return_value=(True, None))
    @patch('smtplib.SMTP')
    def test_dispatches_through_send_notification(self, mock_smtp, _host):
        mock_smtp.return_value.__enter__.return_value = mock_smtp.return_value

        success, error = self._service().send_notification(
            provider_type='email', provider_config=BASE_CONFIG,
            title='Test', message='body'
        )

        assert (success, error) == (True, None)


class TestEmailConfigValidation:

    def test_email_is_a_valid_provider_type(self):
        assert 'email' in VALID_PROVIDER_TYPES

    @patch('pixelprobe.api.notification_routes.validate_outbound_host',
           return_value=(True, None))
    def test_valid_config_accepted(self, _host):
        assert _validate_provider_config('email', BASE_CONFIG) is None

    @patch('pixelprobe.api.notification_routes.validate_outbound_host',
           return_value=(True, None))
    def test_comma_separated_recipients_accepted(self, _host):
        config = {**BASE_CONFIG, 'recipients': 'a@b.com, c@d.com'}
        assert _validate_provider_config('email', config) is None

    def test_missing_host_rejected(self):
        config = {k: v for k, v in BASE_CONFIG.items() if k != 'smtp_host'}
        assert 'SMTP host is required' == _validate_provider_config('email', config)

    def test_missing_recipients_rejected(self):
        config = {**BASE_CONFIG, 'recipients': []}
        assert 'recipient' in _validate_provider_config('email', config)

    def test_non_numeric_port_rejected(self):
        config = {**BASE_CONFIG, 'smtp_port': 'abc'}
        assert 'number' in _validate_provider_config('email', config)

    def test_out_of_range_port_rejected(self):
        config = {**BASE_CONFIG, 'smtp_port': 70000}
        assert 'between 1 and 65535' in _validate_provider_config('email', config)

    @patch('pixelprobe.api.notification_routes.validate_outbound_host',
           return_value=(False, 'Host is a blocked cloud metadata address'))
    def test_blocked_host_rejected(self, _host):
        error = _validate_provider_config('email', BASE_CONFIG)
        assert 'blocked by security policy' in error


class TestPreserveMaskedSecrets:

    def test_masked_value_keeps_stored_secret(self):
        stored = {'smtp_host': 'smtp.example.com', 'password': 'real-secret'}
        incoming = {'smtp_host': 'smtp2.example.com', 'password': '***'}
        assert _preserve_masked_secrets(stored, incoming) == {
            'smtp_host': 'smtp2.example.com', 'password': 'real-secret'
        }

    def test_real_new_secret_is_written(self):
        stored = {'password': 'old'}
        assert _preserve_masked_secrets(stored, {'password': 'new'})['password'] == 'new'

    def test_mask_for_unknown_key_passes_through(self):
        assert _preserve_masked_secrets({}, {'token': '***'})['token'] == '***'

    def test_applies_to_other_provider_types(self):
        stored = {'api_token': 'real', 'user_key': 'realkey'}
        incoming = {'api_token': '***', 'user_key': '***'}
        assert _preserve_masked_secrets(stored, incoming) == stored

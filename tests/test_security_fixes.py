"""
Tests for security fixes in v2.5.64+:
- Authentication bypass via X-Internal-Request header
- SSRF via healthcheck and webhook URLs
- Trusted internal hosts allowlist (v2.5.66)
"""

import os
import pytest
from unittest.mock import patch, MagicMock
import requests


# ==================== Auth Bypass Tests ====================


class TestAuthBypassFix:
    """Tests that the old X-Internal-Request: scheduler header no longer bypasses auth"""

    def test_old_header_no_longer_bypasses_auth(self, app):
        """The old spoofable header must NOT grant access"""
        with app.test_client() as client:
            response = client.get(
                '/api/version',
                headers={'X-Internal-Request': 'scheduler'}
            )
            assert response.status_code == 401

    def test_correct_internal_secret_grants_access(self, app):
        """Correct X-Internal-Secret value should grant access"""
        secret = app.config.get('INTERNAL_API_SECRET', 'test-internal-secret')
        with app.test_client() as client:
            response = client.get(
                '/api/version',
                headers={'X-Internal-Secret': secret}
            )
            assert response.status_code == 200

    def test_wrong_internal_secret_returns_401(self, app):
        """Wrong secret must be rejected"""
        with app.test_client() as client:
            response = client.get(
                '/api/version',
                headers={'X-Internal-Secret': 'wrong-secret-value'}
            )
            assert response.status_code == 401

    def test_empty_internal_secret_returns_401(self, app):
        """Empty secret header must be rejected"""
        with app.test_client() as client:
            response = client.get(
                '/api/version',
                headers={'X-Internal-Secret': ''}
            )
            assert response.status_code == 401

    def test_no_auth_header_returns_401(self, app):
        """No auth header at all must be rejected"""
        with app.test_client() as client:
            response = client.get('/api/version')
            assert response.status_code == 401


# ==================== SSRF Tests ====================


class TestValidateSafeUrl:
    """Tests for validate_safe_url() SSRF protection"""

    def test_rejects_loopback_ipv4(self):
        from pixelprobe.utils.security import validate_safe_url
        is_safe, error = validate_safe_url('http://127.0.0.1/')
        assert not is_safe
        assert 'private' in error.lower() or 'reserved' in error.lower()

    def test_rejects_cloud_metadata(self):
        from pixelprobe.utils.security import validate_safe_url
        is_safe, error = validate_safe_url('http://169.254.169.254/')
        assert not is_safe

    def test_rejects_rfc1918_10(self):
        from pixelprobe.utils.security import validate_safe_url
        is_safe, error = validate_safe_url('http://10.0.0.1/')
        assert not is_safe

    def test_rejects_rfc1918_192(self):
        from pixelprobe.utils.security import validate_safe_url
        is_safe, error = validate_safe_url('http://192.168.1.1/')
        assert not is_safe

    def test_rejects_ipv6_loopback(self):
        from pixelprobe.utils.security import validate_safe_url
        is_safe, error = validate_safe_url('http://[::1]/')
        assert not is_safe

    def test_rejects_zero_address(self):
        from pixelprobe.utils.security import validate_safe_url
        is_safe, error = validate_safe_url('http://0.0.0.0/')
        assert not is_safe

    def test_rejects_empty_url(self):
        from pixelprobe.utils.security import validate_safe_url
        is_safe, error = validate_safe_url('')
        assert not is_safe
        assert 'empty' in error.lower()

    def test_rejects_ftp_scheme(self):
        from pixelprobe.utils.security import validate_safe_url
        is_safe, error = validate_safe_url('ftp://example.com/')
        assert not is_safe
        assert 'scheme' in error.lower()

    def test_rejects_credentials_in_url(self):
        from pixelprobe.utils.security import validate_safe_url
        is_safe, error = validate_safe_url('http://user:pass@example.com/')
        assert not is_safe
        assert 'credential' in error.lower()

    @patch('pixelprobe.utils.security.socket.getaddrinfo')
    def test_accepts_public_url(self, mock_getaddrinfo):
        """Public URLs should be accepted (mock DNS to avoid network calls)"""
        from pixelprobe.utils.security import validate_safe_url
        # Mock DNS resolution to return a public IP
        mock_getaddrinfo.return_value = [
            (2, 1, 6, '', ('93.184.216.34', 80))
        ]
        is_safe, error = validate_safe_url('http://example.com/')
        assert is_safe
        assert error is None

    @patch('pixelprobe.utils.security.socket.getaddrinfo')
    def test_accepts_https_url(self, mock_getaddrinfo):
        """HTTPS URLs with public IPs should be accepted"""
        from pixelprobe.utils.security import validate_safe_url
        mock_getaddrinfo.return_value = [
            (2, 1, 6, '', ('93.184.216.34', 443))
        ]
        is_safe, error = validate_safe_url('https://hc-ping.com/abc-123')
        assert is_safe
        assert error is None

    def test_rejects_rfc1918_172(self):
        from pixelprobe.utils.security import validate_safe_url
        is_safe, error = validate_safe_url('http://172.16.0.1/')
        assert not is_safe


class TestCreateSafeSession:
    """Tests for create_safe_session()"""

    def test_session_has_max_redirects(self):
        from pixelprobe.utils.security import create_safe_session
        session = create_safe_session()
        assert session.max_redirects == 5

    def test_custom_max_redirects(self):
        from pixelprobe.utils.security import create_safe_session
        session = create_safe_session(max_redirects=3)
        assert session.max_redirects == 3

    def test_session_has_response_hook(self):
        from pixelprobe.utils.security import create_safe_session
        session = create_safe_session()
        assert len(session.hooks['response']) >= 1


class TestNotificationRoutesValidation:
    """Tests for SSRF validation in notification route config validation"""

    @patch('pixelprobe.api.notification_routes.validate_safe_url')
    def test_webhook_private_ip_rejected(self, mock_validate):
        """Webhook URLs targeting private IPs should be rejected"""
        mock_validate.return_value = (False, "URL resolves to a private/reserved IP address")
        from pixelprobe.api.notification_routes import _validate_provider_config

        error = _validate_provider_config('webhook', {
            'webhook_url': 'http://192.168.1.1/hook'
        })
        assert error is not None
        assert 'blocked' in error.lower() or 'private' in error.lower()

    @patch('pixelprobe.api.notification_routes.validate_safe_url')
    def test_ntfy_private_ip_rejected(self, mock_validate):
        """ntfy server URLs targeting private IPs should be rejected"""
        mock_validate.return_value = (False, "URL resolves to a private/reserved IP address")
        from pixelprobe.api.notification_routes import _validate_provider_config

        error = _validate_provider_config('ntfy', {
            'server_url': 'http://10.0.0.1',
            'topic': 'test'
        })
        assert error is not None
        assert 'blocked' in error.lower() or 'private' in error.lower()

    def test_ntfy_accepts_server_url_field(self):
        """The server_url field name should be accepted for ntfy config"""
        from pixelprobe.api.notification_routes import _validate_provider_config

        with patch('pixelprobe.api.notification_routes.validate_safe_url') as mock_validate:
            mock_validate.return_value = (True, None)
            error = _validate_provider_config('ntfy', {
                'server_url': 'https://ntfy.example.com',
                'topic': 'test'
            })
            assert error is None

    def test_ntfy_accepts_legacy_server_field(self):
        """The legacy 'server' field name should still be accepted for backward compat"""
        from pixelprobe.api.notification_routes import _validate_provider_config

        with patch('pixelprobe.api.notification_routes.validate_safe_url') as mock_validate:
            mock_validate.return_value = (True, None)
            error = _validate_provider_config('ntfy', {
                'server': 'https://ntfy.example.com',
                'topic': 'test'
            })
            assert error is None

    @patch('pixelprobe.api.notification_routes.validate_safe_url')
    def test_webhook_public_url_accepted(self, mock_validate):
        """Webhook URLs targeting public IPs should pass validation"""
        mock_validate.return_value = (True, None)
        from pixelprobe.api.notification_routes import _validate_provider_config

        error = _validate_provider_config('webhook', {
            'webhook_url': 'https://hooks.slack.com/services/T00/B00/xxxx'
        })
        assert error is None


# ==================== Trusted Internal Hosts Tests (v2.5.66) ====================


class TestTrustedInternalHosts:
    """Tests for TRUSTED_INTERNAL_HOSTS allowlist bypassing SSRF private-IP blocking"""

    def setup_method(self):
        """Reset the trusted hosts cache before each test"""
        from pixelprobe.utils.security import _reset_trusted_hosts
        _reset_trusted_hosts()

    def teardown_method(self):
        """Reset the cache and env var after each test"""
        from pixelprobe.utils.security import _reset_trusted_hosts
        _reset_trusted_hosts()
        os.environ.pop('TRUSTED_INTERNAL_HOSTS', None)

    @patch('pixelprobe.utils.security.socket.getaddrinfo')
    def test_trusted_hostname_bypasses_ssrf_block(self, mock_getaddrinfo):
        """A hostname in TRUSTED_INTERNAL_HOSTS should be allowed even if it resolves to a private IP"""
        from pixelprobe.utils.security import validate_safe_url
        os.environ['TRUSTED_INTERNAL_HOSTS'] = 'healthcheck.internal.local'
        mock_getaddrinfo.return_value = [
            (2, 1, 6, '', ('192.168.5.33', 80))
        ]
        is_safe, error = validate_safe_url('http://healthcheck.internal.local/ping/abc')
        assert is_safe, f"Expected safe but got error: {error}"
        assert error is None

    @patch('pixelprobe.utils.security.socket.getaddrinfo')
    def test_trusted_cidr_bypasses_ssrf_block(self, mock_getaddrinfo):
        """An IP within a trusted CIDR range should be allowed"""
        from pixelprobe.utils.security import validate_safe_url
        os.environ['TRUSTED_INTERNAL_HOSTS'] = '192.168.5.0/24'
        mock_getaddrinfo.return_value = [
            (2, 1, 6, '', ('192.168.5.33', 80))
        ]
        is_safe, error = validate_safe_url('http://internal-service.local/api')
        assert is_safe, f"Expected safe but got error: {error}"
        assert error is None

    @patch('pixelprobe.utils.security.socket.getaddrinfo')
    def test_trusted_bare_ip_bypasses_ssrf_block(self, mock_getaddrinfo):
        """A single trusted IP (no CIDR suffix) should be treated as /32 and allowed"""
        from pixelprobe.utils.security import validate_safe_url
        os.environ['TRUSTED_INTERNAL_HOSTS'] = '10.0.0.5'
        mock_getaddrinfo.return_value = [
            (2, 1, 6, '', ('10.0.0.5', 443))
        ]
        is_safe, error = validate_safe_url('https://10.0.0.5/webhook')
        assert is_safe, f"Expected safe but got error: {error}"
        assert error is None

    @patch('pixelprobe.utils.security.socket.getaddrinfo')
    def test_non_trusted_private_ip_still_blocked(self, mock_getaddrinfo):
        """Private IPs NOT in the trusted list must still be blocked"""
        from pixelprobe.utils.security import validate_safe_url
        os.environ['TRUSTED_INTERNAL_HOSTS'] = 'healthcheck.internal.local'
        mock_getaddrinfo.return_value = [
            (2, 1, 6, '', ('192.168.1.1', 80))
        ]
        is_safe, error = validate_safe_url('http://evil.internal/')
        assert not is_safe
        assert 'private' in error.lower() or 'reserved' in error.lower()

    @patch('pixelprobe.utils.security.socket.getaddrinfo')
    def test_empty_trusted_hosts_preserves_blocking(self, mock_getaddrinfo):
        """When TRUSTED_INTERNAL_HOSTS is empty/unset, all private IPs should be blocked"""
        from pixelprobe.utils.security import validate_safe_url
        os.environ.pop('TRUSTED_INTERNAL_HOSTS', None)
        mock_getaddrinfo.return_value = [
            (2, 1, 6, '', ('192.168.5.33', 80))
        ]
        is_safe, error = validate_safe_url('http://healthcheck.internal.local/ping/abc')
        assert not is_safe
        assert 'private' in error.lower() or 'reserved' in error.lower()

    @patch('pixelprobe.utils.security.socket.getaddrinfo')
    def test_trusted_hostname_case_insensitive(self, mock_getaddrinfo):
        """Hostname matching should be case-insensitive"""
        from pixelprobe.utils.security import validate_safe_url
        os.environ['TRUSTED_INTERNAL_HOSTS'] = 'HealthCheck.Internal.Local'
        mock_getaddrinfo.return_value = [
            (2, 1, 6, '', ('192.168.5.33', 80))
        ]
        is_safe, error = validate_safe_url('http://healthcheck.internal.local/ping/abc')
        assert is_safe, f"Expected safe but got error: {error}"

    @patch('pixelprobe.utils.security.socket.getaddrinfo')
    def test_multiple_trusted_entries(self, mock_getaddrinfo):
        """Multiple comma-separated entries should all be recognized"""
        from pixelprobe.utils.security import validate_safe_url
        os.environ['TRUSTED_INTERNAL_HOSTS'] = 'healthcheck.internal.local, ntfy.internal, 10.0.0.0/8'
        # Test hostname match
        mock_getaddrinfo.return_value = [
            (2, 1, 6, '', ('192.168.5.33', 80))
        ]
        is_safe, _ = validate_safe_url('http://healthcheck.internal.local/ping')
        assert is_safe

    @patch('pixelprobe.utils.security.socket.getaddrinfo')
    def test_ip_outside_trusted_cidr_still_blocked(self, mock_getaddrinfo):
        """An IP outside the trusted CIDR range must still be blocked"""
        from pixelprobe.utils.security import validate_safe_url
        os.environ['TRUSTED_INTERNAL_HOSTS'] = '192.168.5.0/24'
        mock_getaddrinfo.return_value = [
            (2, 1, 6, '', ('192.168.6.1', 80))
        ]
        is_safe, error = validate_safe_url('http://other.internal/')
        assert not is_safe

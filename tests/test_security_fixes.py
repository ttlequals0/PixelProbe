"""
Tests for security fixes in v2.5.64+:
- Authentication bypass via X-Internal-Request header
- SSRF via healthcheck and webhook URLs
- Trusted internal hosts allowlist (v2.5.66)
"""

import os
import sys
import hashlib
import pytest
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace
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


class TestBoundOutboundConnections:
    @patch('pixelprobe.utils.security.socket.getaddrinfo')
    @patch('pixelprobe.utils.security.HTTPConnection._new_conn', autospec=True)
    def test_https_connects_vetted_ip_but_preserves_tls_hostname(self, parent_connect, dns):
        from pixelprobe.utils.security import _BoundHTTPSConnection
        dns.return_value = [(2, 1, 6, '', ('93.184.216.34', 443))]
        captured = []
        parent_connect.side_effect = lambda conn: captured.append(conn._dns_host) or object()
        connection = _BoundHTTPSConnection('origin.example', port=443)

        connection._new_conn()
        assert captured == ['93.184.216.34']
        assert connection.host == 'origin.example'

    @patch('pixelprobe.utils.security.socket.getaddrinfo')
    @patch('pixelprobe.utils.security.HTTPConnection._new_conn', autospec=True)
    def test_rebound_private_address_never_reaches_connect(self, parent_connect, dns):
        from pixelprobe.utils.security import _BoundHTTPSConnection
        dns.return_value = [(2, 1, 6, '', ('127.0.0.1', 443))]
        connection = _BoundHTTPSConnection('rebound.example', port=443)

        with pytest.raises(OSError):
            connection._new_conn()
        parent_connect.assert_not_called()


class TestMappedSSRFAddresses:
    @pytest.mark.parametrize('url', [
        'http://[::ffff:127.0.0.1]/',
        'http://[::ffff:169.254.169.254]/',
        'http://100.100.100.200/',
    ])
    def test_rejects_mapped_and_metadata_addresses(self, url):
        from pixelprobe.utils.security import validate_safe_url
        is_safe, _ = validate_safe_url(url)
        assert not is_safe


class TestMediaServingBoundaries:
    def _result(self, db, path, mime='image/jpeg'):
        from pixelprobe.models import ScanResult
        result = ScanResult(file_path=str(path), file_size=path.stat().st_size,
                            file_type=mime, scan_status='completed')
        db.session.add(result)
        db.session.commit()
        return result

    def _root(self, db, root):
        from pixelprobe.models import ScanConfiguration
        db.session.add(ScanConfiguration(path=str(root), is_active=True))
        db.session.commit()

    def test_view_download_and_ranges_use_authorized_descriptor(self, app, db,
                                                                 authenticated_client, tmp_path):
        root = tmp_path / 'media'
        root.mkdir()
        media = root / 'sample.jpg'
        media.write_bytes(b'0123456789')
        with app.app_context():
            self._root(db, root)
            result = self._result(db, media)
            result_id = result.id

        view = authenticated_client.get(f'/api/view/{result_id}')
        assert view.status_code == 200
        assert view.data == b'0123456789'
        assert view.headers['Content-Length'] == '10'
        assert view.headers['X-Content-Type-Options'] == 'nosniff'
        download = authenticated_client.get(f'/api/download/{result_id}')
        assert download.status_code == 200
        assert 'attachment' in download.headers['Content-Disposition']

        cases = {
            'bytes=2-5': (206, b'2345', 'bytes 2-5/10'),
            'bytes=8-20': (206, b'89', 'bytes 8-9/10'),
            'bytes=-3': (206, b'789', 'bytes 7-9/10'),
        }
        for range_header, (status, body, content_range) in cases.items():
            response = authenticated_client.get(f'/api/view/{result_id}',
                                                headers={'Range': range_header})
            assert response.status_code == status
            assert response.data == body
            assert response.headers['Content-Range'] == content_range
            assert response.headers['Content-Length'] == str(len(body))
        for range_header in ('bytes=10-', 'bytes=8-2'):
            response = authenticated_client.get(f'/api/view/{result_id}',
                                                headers={'Range': range_header})
            assert response.status_code == 416
            assert response.headers['Content-Range'] == 'bytes */10'

    def test_unsatisfiable_ranges_close_each_opened_descriptor(self, app, db,
                                                                authenticated_client, tmp_path,
                                                                monkeypatch):
        root = tmp_path / 'media'
        root.mkdir()
        media = root / 'sample.jpg'
        media.write_bytes(b'0123456789')
        with app.app_context():
            self._root(db, root)
            result_id = self._result(db, media).id

        from pixelprobe.api import export_routes
        original_open = export_routes.open_authorized_media_file
        opened = []

        def tracked_open(*args, **kwargs):
            media_file, canonical, file_stat = original_open(*args, **kwargs)
            opened.append(media_file)
            return media_file, canonical, file_stat

        monkeypatch.setattr(export_routes, 'open_authorized_media_file', tracked_open)
        for _ in range(3):
            response = authenticated_client.get(f'/api/view/{result_id}',
                                                headers={'Range': 'bytes=10-'})
            assert response.status_code == 416
        assert len(opened) == 3
        assert all(media_file.closed for media_file in opened)

    def test_required_mount_mismatch_blocks_cleanup_before_it_starts(self, app, db,
                                                                     authenticated_client, tmp_path,
                                                                     monkeypatch):
        root = tmp_path / 'media'
        root.mkdir()
        with app.app_context():
            from pixelprobe.models import ScanConfiguration
            db.session.add(ScanConfiguration(
                path=str(root), is_active=True, require_mount=True,
                mount_filesystem_type='nfs', mount_source='server:/library', mount_root='/',
            ))
            db.session.commit()

        monkeypatch.setattr('pixelprobe.api.maintenance_routes.required_mounts_available',
                            lambda _paths: False)
        response = authenticated_client.post('/api/cleanup-orphaned', json={
            'scan_roots': [str(root)],
        })
        assert response.status_code == 503
        with app.app_context():
            from pixelprobe.models import CleanupState
            assert CleanupState.query.filter_by(is_active=True).count() == 0

    def test_required_mount_baseline_is_explicit_and_not_replaced_implicitly(
            self, app, db, authenticated_client, tmp_path, monkeypatch):
        root = tmp_path / 'media'
        root.mkdir()
        observed = {
            'filesystem_type': 'nfs', 'source': 'server:/library', 'root': '/',
            'mount_point': str(root),
        }
        monkeypatch.setattr('pixelprobe.api.admin_routes.observe_mount', lambda _path: observed)
        created = authenticated_client.post('/api/configurations', json={
            'path': str(root), 'require_mount': True,
        })
        assert created.status_code == 200
        observed['source'] = 'other-server:/library'
        reactivated = authenticated_client.post('/api/configurations', json={
            'path': str(root), 'require_mount': True,
        })
        assert reactivated.status_code == 200
        with app.app_context():
            from pixelprobe.models import ScanConfiguration
            config = ScanConfiguration.query.filter_by(path=str(root)).one()
            assert config.mount_source == 'server:/library'
        preserved = authenticated_client.post('/api/configurations', json={'path': str(root)})
        assert preserved.status_code == 200
        with app.app_context():
            assert ScanConfiguration.query.filter_by(path=str(root)).one().require_mount is True
        refreshed = authenticated_client.post('/api/configurations', json={
            'path': str(root), 'require_mount': True, 'refresh_mount_baseline': True,
        })
        assert refreshed.status_code == 200
        with app.app_context():
            from pixelprobe.models import ScanConfiguration
            assert ScanConfiguration.query.filter_by(path=str(root)).one().mount_source == 'other-server:/library'

    def test_stale_or_symlinked_database_rows_cannot_grant_file_access(self, app, db,
                                                                        authenticated_client, tmp_path):
        root = tmp_path / 'media'
        root.mkdir()
        outside = tmp_path / 'outside.jpg'
        outside.write_bytes(b'outside')
        escaped = root / 'escaped.jpg'
        escaped.symlink_to(outside)
        unsupported = root / 'notes.txt'
        unsupported.write_text('not media')
        with app.app_context():
            self._root(db, root)
            outside_result = self._result(db, outside)
            escaped_result = self._result(db, escaped)
            unsupported_result = self._result(db, unsupported)
            ids = (outside_result.id, escaped_result.id, unsupported_result.id)

        for result_id in ids:
            assert authenticated_client.get(f'/api/view/{result_id}').status_code == 404
            assert authenticated_client.get(f'/api/download/{result_id}').status_code == 404

    def test_authorized_open_rejects_final_symlink(self, tmp_path):
        from pixelprobe.utils.security import PathTraversalError, open_authorized_media_file

        root = tmp_path / 'media'
        root.mkdir()
        outside = tmp_path / 'outside.jpg'
        outside.write_bytes(b'outside')
        (root / 'escaped.jpg').symlink_to(outside)

        with pytest.raises(PathTraversalError):
            open_authorized_media_file(str(root / 'escaped.jpg'), [str(root)])

    def test_authorized_open_requires_the_selected_root_even_when_another_is_active(self, tmp_path):
        from pixelprobe.utils.security import PathTraversalError, open_authorized_media_file

        selected_root = tmp_path / 'selected'
        other_root = tmp_path / 'other'
        selected_root.mkdir()
        other_root.mkdir()
        media = other_root / 'sample.jpg'
        media.write_bytes(b'other-root')
        (selected_root / 'linked.jpg').symlink_to(media)

        with pytest.raises(PathTraversalError):
            open_authorized_media_file(
                str(selected_root / 'linked.jpg'),
                [str(selected_root), str(other_root)],
                required_paths=[str(selected_root)],
            )

    def test_scanner_keeps_opened_bytes_when_ancestor_is_replaced(self, tmp_path):
        from PIL import Image
        from pixelprobe.media_checker import PixelProbe

        root = tmp_path / 'media'
        nested = root / 'nested'
        outside = tmp_path / 'outside'
        nested.mkdir(parents=True)
        outside.mkdir()
        media = nested / 'sample.png'
        Image.new('RGB', (2, 2), 'red').save(media)
        original_hash = hashlib.sha256(media.read_bytes()).hexdigest()
        Image.new('RGB', (2, 2), 'blue').save(outside / 'sample.png')

        checker = PixelProbe(allowed_paths=[str(root)])
        get_file_info = checker.get_file_info
        replaced = False

        def replace_ancestor(checked_path, *args, **kwargs):
            nonlocal replaced
            if not replaced:
                moved = root / 'moved'
                nested.rename(moved)
                nested.symlink_to(outside, target_is_directory=True)
                replaced = True
            return get_file_info(checked_path, *args, **kwargs)

        checker.get_file_info = replace_ancestor
        result = checker.scan_file(str(media), force_rescan=True)

        assert result['outcome'] == 'completed'
        assert result['file_path'] == str(media)
        assert result['file_hash'] == original_hash
        assert 'ImageMagick convert: PASSED' in result['scan_output']

    def test_ffprobe_and_ffmpeg_read_opened_media_after_ancestor_replacement(self, tmp_path):
        from pixelprobe.utils.security import (
            authorized_fd_path, open_authorized_media_file, safe_subprocess_run,
        )

        root = tmp_path / 'media'
        nested = root / 'nested'
        nested.mkdir(parents=True)
        media = nested / 'sample.mp4'
        fixture = Path(__file__).parent / 'fixtures' / 'media_samples' / 'valid.mp4'
        shutil.copyfile(fixture, media)
        opened, _, _ = open_authorized_media_file(str(media), [str(root)])
        fd_path = authorized_fd_path(opened.fileno())
        moved = root / 'moved'
        nested.rename(moved)
        nested.symlink_to(tmp_path / 'outside', target_is_directory=True)

        try:
            probe = safe_subprocess_run([
                'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1', fd_path,
            ], capture_output=True, text=True, timeout=20)
            decode = safe_subprocess_run([
                'ffmpeg', '-v', 'error', '-i', fd_path, '-f', 'null', '-',
            ], capture_output=True, text=True, timeout=20)
        finally:
            opened.close()

        assert probe.returncode == 0
        assert 'duration=' in probe.stdout
        assert decode.returncode == 0

    def test_timed_out_reader_keeps_its_descriptor_after_scan_fd_closes(self, tmp_path):
        from pixelprobe.media_checker import FileReadTimeoutError, _read_with_timeout
        from pixelprobe.utils.security import authorized_fd_path, open_authorized_media_file

        root = tmp_path / 'media'
        root.mkdir()
        media = root / 'sample.jpg'
        media.write_bytes(b'original-bytes')
        replacement = root / 'replacement.jpg'
        replacement.write_bytes(b'replacement-bytes')
        opened, _, _ = open_authorized_media_file(str(media), [str(root)])
        fd_path = authorized_fd_path(opened.fileno())
        release_reader = threading.Event()
        reader_done = threading.Event()
        observed = {}

        def blocked_reader(reader_path):
            release_reader.wait(1)
            with open(reader_path, 'rb') as duplicate:
                observed['bytes'] = duplicate.read()
            reader_done.set()

        with pytest.raises(FileReadTimeoutError):
            _read_with_timeout(
                blocked_reader, 0.01, str(media), 'test read', descriptor_path=fd_path)
        original_fd = opened.fileno()
        opened.close()
        reused = open(replacement, 'rb')
        assert reused.fileno() == original_fd
        release_reader.set()
        assert reader_done.wait(1)
        assert observed['bytes'] == b'original-bytes'
        reused.close()

    @pytest.mark.parametrize('filename,mime', [
        ('payload.svg', 'image/svg+xml'),
        ('payload.jpg', 'text/html'),
    ])
    def test_active_or_untrusted_mime_is_downloaded(self, app, db,
                                                    authenticated_client, tmp_path,
                                                    filename, mime):
        root = tmp_path / 'media'
        root.mkdir()
        media = root / filename
        media.write_bytes(b'<html>not inert</html>')
        with app.app_context():
            self._root(db, root)
            result = self._result(db, media, mime)
            result_id = result.id
        response = authenticated_client.get(f'/api/view/{result_id}')
        assert response.status_code == 200
        assert 'attachment' in response.headers['Content-Disposition']
        assert response.headers['Content-Type'].startswith('application/octet-stream')


class TestSecurityAuditPersistence:
    def test_actor_event_survives_routine_log_purge(self, app):
        from pixelprobe.models import db, LogEntry, SecurityAuditEvent, User
        from pixelprobe.utils.security import AuditLogger
        with app.app_context():
            db.create_all()
            user = User(username='audit-actor', email='audit@example.test')
            user.set_password('test-password')
            db.session.add(user)
            db.session.commit()
            AuditLogger.log_action('admin_setting_changed', user=user,
                                   target='setting:scanner.timeout',
                                   details={'token': 'must-not-persist'})
            db.session.add(LogEntry(level='INFO', message='ordinary log'))
            db.session.commit()
            LogEntry.query.delete()
            db.session.commit()

            event = SecurityAuditEvent.query.filter_by(action='admin_setting_changed').one()
            assert event.actor_id == user.id
            assert event.target == 'setting:scanner.timeout'
            assert event.details['token'] == '[redacted]'
            assert SecurityAuditEvent.query.filter_by(action='admin_setting_changed').count() == 1

    def test_deleting_actor_keeps_audit_evidence(self, app):
        from pixelprobe.models import db, SecurityAuditEvent, User
        with app.app_context():
            db.create_all()
            user = User(username='deleted-audit-actor', email='deleted-audit@example.test')
            user.set_password('test-password')
            db.session.add(user)
            db.session.commit()
            actor_id = user.id
            db.session.add(SecurityAuditEvent(
                actor_id=actor_id, action='login_failed', outcome='denied', details={},
            ))
            db.session.commit()

            db.session.delete(user)
            db.session.commit()

            event = SecurityAuditEvent.query.filter_by(actor_id=actor_id).one()
            assert event.action == 'login_failed'


class TestCapabilityUrlLogRedaction:
    def test_webhook_failure_never_logs_capability_url(self, caplog):
        from pixelprobe.services.notification_service import NotificationService
        service = NotificationService()
        service.session.post = MagicMock(side_effect=requests.RequestException(
            'https://hooks.example.invalid/capability-secret'
        ))
        with caplog.at_level('ERROR'):
            success, _ = service._send_webhook(
                {'webhook_url': 'https://hooks.example.invalid/capability-secret'},
                'title', 'message', 'normal', None
            )
        assert not success
        assert 'capability-secret' not in caplog.text

    def test_healthcheck_validation_never_logs_capability_url(self, caplog, monkeypatch):
        from pixelprobe.services.healthcheck_service import HealthcheckService
        monkeypatch.setattr('pixelprobe.services.healthcheck_service.validate_safe_url',
                            lambda _url: (True, None))
        with caplog.at_level('DEBUG'):
            valid, _ = HealthcheckService().validate_url(
                'https://health.example.invalid/capability-secret')
        assert valid
        assert 'capability-secret' not in caplog.text

    def test_invalid_redis_url_never_logs_credentials(self, caplog, monkeypatch):
        from pixelprobe import progress_utils
        monkeypatch.setenv('CELERY_BROKER_URL', 'invalid://user:capability-secret@host')
        with caplog.at_level('ERROR'):
            assert progress_utils.get_redis_client() is None
        assert 'capability-secret' not in caplog.text

    def test_worker_startup_never_logs_broker_credentials(self, caplog, monkeypatch):
        fake_celery = MagicMock()
        fake_celery.worker_main.return_value = 0
        monkeypatch.setitem(sys.modules, 'app', SimpleNamespace(celery=fake_celery))
        monkeypatch.delitem(sys.modules, 'celery_worker', raising=False)
        import celery_worker
        monkeypatch.setenv('CELERY_BROKER_URL', 'redis://user:capability-secret@host/0')
        monkeypatch.setenv('CELERY_RESULT_BACKEND', 'redis://user:capability-secret@host/1')
        with caplog.at_level('INFO'), pytest.raises(SystemExit):
            celery_worker.main()
        assert 'capability-secret' not in caplog.text


class TestCookieSecurityConfiguration:
    def test_production_defaults_secure_and_remember_cookie(self, monkeypatch):
        from flask import Flask
        from pixelprobe.auth import init_auth
        monkeypatch.delenv('SESSION_COOKIE_SECURE', raising=False)
        app = Flask(__name__)
        app.config['SECRET_KEY'] = 'test'
        init_auth(app)
        assert app.config['SESSION_COOKIE_SECURE'] is True
        assert app.config['REMEMBER_COOKIE_SECURE'] is True
        assert app.config['SESSION_COOKIE_HTTPONLY'] is True
        assert app.config['REMEMBER_COOKIE_HTTPONLY'] is True

    def test_explicit_http_override_applies_to_both_cookie_types(self, monkeypatch):
        from flask import Flask
        from pixelprobe.auth import init_auth
        monkeypatch.setenv('SESSION_COOKIE_SECURE', 'false')
        app = Flask(__name__)
        app.config['SECRET_KEY'] = 'test'
        init_auth(app)
        assert app.config['SESSION_COOKIE_SECURE'] is False
        assert app.config['REMEMBER_COOKIE_SECURE'] is False


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


# ==================== Subprocess timeout hardening (v2.6.44) ====================


class TestSafeSubprocessTimeout:
    """safe_subprocess_run must bound runtime and isolate the child session so a
    stalled ffmpeg/ffprobe cannot hang a scan worker forever."""

    def test_timeout_raises(self):
        import subprocess
        from pixelprobe.utils.security import safe_subprocess_run
        with pytest.raises(subprocess.TimeoutExpired):
            safe_subprocess_run(['sleep', '5'], timeout=1)

    def test_start_new_session_set_on_posix(self):
        import os
        from unittest.mock import patch as _patch
        from pixelprobe.utils.security import safe_subprocess_run
        with _patch('subprocess.run') as mock_run:
            safe_subprocess_run(['echo', 'hi'], capture_output=True)
            _, kwargs = mock_run.call_args
            if os.name == 'posix':
                assert kwargs.get('start_new_session') is True

    def test_ffprobe_with_timeout_raises_on_missing_file(self):
        import ffmpeg
        from pixelprobe.media_checker import _ffprobe_with_timeout
        with pytest.raises(ffmpeg.Error):
            _ffprobe_with_timeout('/nonexistent/path/never.mkv', timeout=10)


class TestSafeSessionRetry:
    """create_safe_session must mount a retry/backoff adapter so transient
    failures don't drop healthcheck pings / notifications (v2.6.47)."""

    def test_session_has_retry_adapter(self):
        from pixelprobe.utils.security import create_safe_session
        session = create_safe_session()
        adapter = session.get_adapter('https://example.com')
        retry = adapter.max_retries
        assert retry.total == 3
        assert 503 in retry.status_forcelist
        assert 429 in retry.status_forcelist
        # Only idempotent GET is retried; POST must not be (avoids duplicate
        # notification delivery on a 5xx-after-delivery).
        assert 'GET' in retry.allowed_methods
        assert 'POST' not in retry.allowed_methods

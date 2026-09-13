"""Integration tests for scan launch claim/release and liveness endpoints"""

import sys
import types
from datetime import datetime, timezone

import pytest

from pixelprobe.models import ScanResult, ScanState, ScanTask, User, db
from pixelprobe.utils.security import PathTraversalError


class TestHealthEndpoints:

    def test_healthz_unauthenticated_returns_200(self, app):
        # Fresh client: like the container healthcheck, no session cookie
        response = app.test_client().get('/healthz')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert 'version' in data

    def test_health_still_requires_auth(self, app):
        response = app.test_client().get('/health')
        assert response.status_code in (401, 302)  # 401 API / 302 login redirect


class TestScanClaimRelease:

    def test_invalid_directory_leaves_no_active_claim(self, authenticated_client, app, db):
        """SCHED-1 regression: a 400 from validation must not leave
        scan_state.is_active=True (which 409-blocked all later scans)."""
        with app.app_context():
            response = authenticated_client.post(
                '/api/scan',
                json={'directories': ['/nonexistent/../etc']}
            )
            assert response.status_code == 400

            active = ScanState.query.filter_by(is_active=True).all()
            assert active == []

    def test_no_directories_configured_leaves_no_active_claim(self, authenticated_client,
                                                              app, db, monkeypatch):
        with app.app_context():
            monkeypatch.setattr('pixelprobe.api.scan_routes.get_configured_scan_paths',
                                lambda: [])
            response = authenticated_client.post('/api/scan', json={})
            assert response.status_code == 400

            active = ScanState.query.filter_by(is_active=True).all()
            assert active == []

    def test_second_scan_conflicts_with_409(self, authenticated_client, app, db,
                                            monkeypatch, tmp_path):
        with app.app_context():
            state = ScanState.get_or_create()
            state.is_active = True
            state.phase = 'scanning'
            db.session.commit()

            # Valid directory so validation passes and the claim path is reached
            monkeypatch.setattr('pixelprobe.api.scan_routes.validate_directory_path',
                                lambda d: d)
            response = authenticated_client.post('/api/scan',
                                                 json={'directories': [str(tmp_path)]})
            assert response.status_code == 409

            state.is_active = False
            state.phase = 'completed'
            db.session.commit()

    def test_periodic_runs_keep_unique_ids_with_a_frozen_clock(self, app, db, monkeypatch, tmp_path):
        from pixelprobe.api import scan_launch

        class FrozenDateTime:
            @staticmethod
            def now(_timezone):
                return datetime(2026, 1, 1, tzinfo=timezone.utc)

        fake_tasks = types.ModuleType('pixelprobe.tasks_parallel')
        fake_tasks.dispatch_scan_task_intent = lambda _intent: True
        monkeypatch.setitem(sys.modules, 'pixelprobe.tasks_parallel', fake_tasks)
        monkeypatch.setattr(scan_launch, 'datetime', FrozenDateTime)
        monkeypatch.setattr(scan_launch, 'check_celery_available', lambda: True)
        with app.app_context():
            first, status = scan_launch.launch_directory_scan(
                [str(tmp_path)], source='scheduled_periodic')
            assert status == 200
            first_id = first['scan_id']
            state = ScanState.query.filter_by(scan_id=first_id).one()
            state.phase = 'completed'
            state.is_active = False
            db.session.commit()

            second, status = scan_launch.launch_directory_scan(
                [str(tmp_path)], source='scheduled_periodic')
            assert status == 200
            assert second['scan_id'] != first_id
            assert second['scan_id'].startswith('scheduled_periodic_20260101_000000_')


class TestScanLaunchResponseShape:
    """CodeQL py/reflective-xss: launch responses must not echo caller-supplied
    directory paths back into the response body."""

    def _launch(self, authenticated_client, monkeypatch, tmp_path, url, module):
        monkeypatch.setattr(f'{module}.validate_directory_path', lambda d: d)
        monkeypatch.setattr(f'{module}.launch_directory_scan',
                            lambda *a, **k: ({'message': 'Scan started'}, 200))
        return authenticated_client.post(url, json={'directories': [str(tmp_path)]})

    def test_parallel_scan_response_omits_directories(self, authenticated_client,
                                                      monkeypatch, tmp_path):
        response = self._launch(authenticated_client, monkeypatch, tmp_path,
                                '/api/scan-files-parallel',
                                'pixelprobe.api.scan_routes')
        assert response.status_code == 200
        assert 'directories' not in response.get_json()

    def test_scan_parallel_v2_response_omits_directories(self, authenticated_client,
                                                         monkeypatch, tmp_path):
        response = self._launch(authenticated_client, monkeypatch, tmp_path,
                                '/api/scan-parallel',
                                'pixelprobe.api.scan_routes_parallel')
        assert response.status_code == 200
        assert 'directories' not in response.get_json()


class TestNumWorkersCap:

    def test_api_num_workers_is_capped(self, authenticated_client, app, db,
                                       monkeypatch, tmp_path):
        """Caller-supplied num_workers sizes thread pools and the checker's
        DB connection pool, so the route must clamp it to MAX_WORKERS."""
        with app.app_context():
            captured = {}

            def fake_scan_files(file_paths, force_rescan=False, num_workers=1, **kwargs):
                captured['num_workers'] = num_workers
                return {'status': 'started', 'num_workers': num_workers}

            monkeypatch.setattr(app.scan_service, 'scan_files', fake_scan_files)
            monkeypatch.setattr('pixelprobe.api.scan_routes.check_celery_available',
                                lambda: False)
            monkeypatch.setattr('pixelprobe.api.scan_routes.resolve_authorized_media_file',
                                lambda path: path)
            media = tmp_path / 'clip.mp4'
            media.write_bytes(b'\x00' * 128)

            response = authenticated_client.post('/api/scan-files-parallel', json={
                'file_paths': [str(media)],
                'num_workers': 999,
            })
            assert response.status_code == 200
            assert captured['num_workers'] <= app.config.get('MAX_WORKERS', 10)
            assert captured['num_workers'] >= 1


class TestSelectedScanAuthorization:

    def test_existing_outside_result_cannot_authorize_single_rescan(
            self, authenticated_client, app, db, monkeypatch):
        with app.app_context():
            path = '/outside/stale.mp4'
            db.session.add(ScanResult(file_path=path, scan_status='completed'))
            db.session.commit()
            called = []

            def reject(candidate):
                called.append(candidate)
                raise PathTraversalError('outside active scan roots')

            monkeypatch.setattr('pixelprobe.api.scan_routes.resolve_authorized_media_file', reject)
            response = authenticated_client.post('/api/scan-file', json={'file_path': path})

            assert response.status_code == 400
            assert called == [path]
            assert ScanState.query.filter_by(is_active=True).count() == 0

    def test_selected_list_rejects_invalid_member_before_dispatch(
            self, authenticated_client, app, db, monkeypatch):
        dispatched = []
        with app.app_context():
            ScanState.query.filter_by(is_active=True).update({'is_active': False})
            db.session.commit()
            task_count = ScanTask.query.count()
        monkeypatch.setattr('pixelprobe.api.scan_routes.resolve_authorized_media_file',
                            lambda path: (_ for _ in ()).throw(PathTraversalError('outside')))
        monkeypatch.setattr('pixelprobe.api.scan_routes.check_celery_available', lambda: True)

        response = authenticated_client.post('/api/scan-files-parallel', json={
            'file_paths': ['/outside/stale.mp4'],
        })

        assert response.status_code == 400
        assert dispatched == []
        with app.app_context():
            assert ScanTask.query.count() == task_count


class TestGlobalScanMutationsRequireAdmin:

    @pytest.mark.parametrize('endpoint', [
        '/api/scan/recovery',
        '/api/reset-for-rescan',
        '/api/force-scan-pending',
        '/api/reset-files-by-path',
        '/api/reset-incomplete-scans',
    ])
    def test_non_admin_is_forbidden(self, app, db, endpoint):
        with app.app_context():
            user = User(username='operator', email='operator@test.com', is_admin=False)
            user.set_password('testpass123')
            db.session.add(user)
            db.session.commit()
        client = app.test_client()
        client.post('/api/auth/login', json={'username': 'operator', 'password': 'testpass123'})
        response = client.post(endpoint, json={})
        assert response.status_code == 403

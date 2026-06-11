"""Integration tests for scan launch claim/release and liveness endpoints"""

from pixelprobe.models import ScanState


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

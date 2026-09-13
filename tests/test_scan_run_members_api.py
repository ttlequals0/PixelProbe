from datetime import datetime, timezone
from uuid import uuid4

from pixelprobe.models import ScanResult, ScanRunFile, ScanState, db


def _run(scan_id, is_active=True):
    state = ScanState(scan_id=scan_id, is_active=is_active,
                      phase='scanning' if is_active else 'cancelled')
    db.session.add(state)
    return state


def test_run_members_are_paginated_immutable_snapshots_without_report(
        app, db, authenticated_client):
    scan_id = str(uuid4())
    other_scan_id = str(uuid4())
    with app.app_context():
        result = ScanResult(file_path='/mutable/current.mp4', scan_status='pending')
        db.session.add(result)
        db.session.flush()
        _run(scan_id)
        _run(other_scan_id, is_active=False)
        db.session.add_all([
            ScanRunFile(scan_id=scan_id, scan_result_id=result.id,
                        file_path='/snapshot/first.mp4', status='completed',
                        outcome='completed', completed_at=datetime.now(timezone.utc)),
            ScanRunFile(scan_id=scan_id, file_path='/snapshot/second.mp4',
                        status='pending', outcome=None),
            ScanRunFile(scan_id=other_scan_id, file_path='/other/run.mp4',
                        status='error', outcome='error'),
        ])
        db.session.commit()
        result.file_path = '/mutable/changed.mp4'
        result.scan_status = 'error'
        db.session.commit()

    first = authenticated_client.get(f'/api/scan-runs/{scan_id}/files?limit=1')
    assert first.status_code == 200
    payload = first.get_json()
    assert payload['scan_id'] == scan_id
    assert payload['is_active'] is True
    assert payload['phase'] == 'scanning'
    assert payload['total_members'] == 2
    assert payload['files'][0]['file_path'] == '/snapshot/first.mp4'
    assert payload['files'][0]['status'] == 'completed'
    assert payload['files'][0]['outcome'] == 'completed'
    assert payload['files'][0]['completed_at'] is not None
    assert payload['next_cursor'] == payload['files'][0]['id']

    second = authenticated_client.get(
        f'/api/scan-runs/{scan_id}/files?cursor={payload["next_cursor"]}&limit=1')
    assert second.status_code == 200
    next_page = second.get_json()
    assert [item['file_path'] for item in next_page['files']] == ['/snapshot/second.mp4']
    assert next_page['files'][0]['status'] == 'pending'
    assert next_page['files'][0]['outcome'] is None
    assert next_page['next_cursor'] is None


def test_run_members_validate_scope_cursor_limit_and_auth(app, db, authenticated_client):
    scan_id = str(uuid4())
    with app.app_context():
        _run(scan_id)
        db.session.commit()

    assert app.test_client().get(
        f'/api/scan-runs/{scan_id}/files',
        headers={'Authorization': 'Bearer invalid-token'},
    ).status_code == 401
    assert authenticated_client.get(f'/api/scan-runs/{uuid4()}/files').status_code == 404
    assert authenticated_client.get('/api/scan-runs/not-a-uuid/files').status_code == 404
    for query in ('cursor=-1', 'cursor=wrong', 'limit=0', 'limit=1001', 'limit=wrong'):
        response = authenticated_client.get(f'/api/scan-runs/{scan_id}/files?{query}')
        assert response.status_code == 400

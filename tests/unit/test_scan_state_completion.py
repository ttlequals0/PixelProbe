from datetime import datetime, timezone

from pixelprobe.models import ScanResult, ScanState


def test_complete_scan_never_mutates_unrelated_pending_result(app, db):
    with app.app_context():
        state = ScanState(scan_id='completion-scope', phase='scanning', is_active=True)
        unrelated = ScanResult(
            file_path='/unrelated/pending.mp4', scan_status='pending',
            discovered_date=datetime.now(timezone.utc),
        )
        db.session.add_all([state, unrelated])
        db.session.commit()

        state.complete_scan()

        assert db.session.get(ScanState, state.id).phase == 'completed'
        assert db.session.get(ScanResult, unrelated.id).scan_status == 'pending'


def test_complete_scan_does_not_override_cancelled_run(app, db):
    with app.app_context():
        state = ScanState(scan_id='completion-cancelled', phase='cancelled', is_active=False)
        db.session.add(state)
        db.session.commit()
        state.complete_scan()
        assert db.session.get(ScanState, state.id).phase == 'cancelled'

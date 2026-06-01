"""Unit tests for startup cleanup routines (phase 3 reliability)."""
from datetime import datetime, timezone, timedelta


class TestCleanupStuckScans:
    """An app-container restart must not crash a scan still progressing in a
    separate worker container; only genuinely stale scans are crashed."""

    def test_recent_scan_survives_restart(self, app, db):
        from pixelprobe.models import ScanState
        from pixelprobe.startup import cleanup_stuck_scans
        with app.app_context():
            live = ScanState(
                scan_id='live-recent', is_active=True, phase='scanning',
                last_update=datetime.now(timezone.utc) - timedelta(seconds=15),
            )
            db.session.add(live)
            db.session.commit()

            cleanup_stuck_scans(db)

            refreshed = ScanState.query.filter_by(scan_id='live-recent').first()
            assert refreshed.is_active is True
            assert refreshed.phase == 'scanning'

    def test_stale_scan_is_crashed(self, app, db):
        from pixelprobe.models import ScanState
        from pixelprobe.startup import cleanup_stuck_scans
        with app.app_context():
            dead = ScanState(
                scan_id='dead-stale', is_active=True, phase='scanning',
                last_update=datetime.now(timezone.utc) - timedelta(hours=2),
            )
            db.session.add(dead)
            db.session.commit()

            cleanup_stuck_scans(db)

            refreshed = ScanState.query.filter_by(scan_id='dead-stale').first()
            assert refreshed.is_active is False
            assert refreshed.phase == 'crashed'

"""Integration tests for the bitrot filter and accept-current-state endpoint."""

from datetime import datetime, timezone

from pixelprobe.models import ScanResult

HASH_A = 'a' * 64
HASH_B = 'b' * 64


def seed(db, path, **kwargs):
    defaults = dict(
        file_path=path,
        file_size=1000,
        file_type='video/mp4',
        scan_status='completed',
        file_hash=HASH_A,
        is_corrupted=False,
        scan_date=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    row = ScanResult(**defaults)
    db.session.add(row)
    db.session.commit()
    return row


class TestBitrotFilter:

    def test_filter_returns_only_flagged(self, authenticated_client, app, db):
        with app.app_context():
            seed(db, '/m/clean.mkv')
            flagged = seed(db, '/m/rotten.mkv', bitrot_suspected=True,
                           bitrot_candidate_hash=HASH_B)

            response = authenticated_client.get('/api/scan-results?bitrot_suspected=true')
            assert response.status_code == 200
            data = response.get_json()
            paths = [r['file_path'] for r in data['results']]
            assert paths == ['/m/rotten.mkv']
            assert data['results'][0]['bitrot_suspected'] is True
            assert data['results'][0]['bitrot_candidate_hash'] == HASH_B
            assert flagged.id == data['results'][0]['id']

    def test_filter_false_excludes_flagged(self, authenticated_client, app, db):
        with app.app_context():
            seed(db, '/m/clean.mkv')
            seed(db, '/m/rotten.mkv', bitrot_suspected=True)

            response = authenticated_client.get('/api/scan-results?bitrot_suspected=false')
            assert response.status_code == 200
            paths = [r['file_path'] for r in response.get_json()['results']]
            assert paths == ['/m/clean.mkv']


class TestIntegrityCoverageStats:

    def test_stats_include_rolling_coverage(self, authenticated_client, app, db):
        with app.app_context():
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            seed(db, '/m/checked_recent.mkv', last_integrity_check_date=now)
            seed(db, '/m/checked_old.mkv',
                 last_integrity_check_date=datetime(2025, 1, 1, 12, 0, 0))
            seed(db, '/m/never.mkv')
            seed(db, '/m/rotten.mkv', bitrot_suspected=True,
                 last_integrity_check_date=now)

            response = authenticated_client.get('/api/stats')
            assert response.status_code == 200
            integrity = response.get_json()['integrity']

            assert integrity['total_files'] == 4
            assert integrity['checked_files'] == 3
            assert integrity['checked_percent'] == 75.0
            assert integrity['never_checked'] == 1
            assert integrity['checked_last_30_days'] == 2
            assert integrity['bitrot_suspected'] == 1
            assert integrity['oldest_check_date'].startswith('2025-01-01')


class TestBitrotAccept:

    def test_accept_adopts_candidate_and_clears_flag(self, authenticated_client, app, db):
        with app.app_context():
            detected = datetime(2026, 7, 1, 12, 0, 0)
            check_mtime = datetime(2026, 7, 1, 11, 0, 0)
            details = ('{"stored_hash": "' + HASH_A + '", "current_hash": "' + HASH_B + '", '
                       '"current_modified": "' + check_mtime.isoformat() + '"}')
            row = seed(db, '/m/rotten.mkv', bitrot_suspected=True,
                       bitrot_candidate_hash=HASH_B, bitrot_stable_checks=1,
                       bitrot_detected_date=detected, bitrot_details=details)

            response = authenticated_client.post('/api/bitrot/accept',
                                                 json={'file_ids': [row.id]})
            assert response.status_code == 200
            assert response.get_json()['accepted'] == 1

            db.session.refresh(row)
            assert row.bitrot_suspected is False
            assert row.file_hash == HASH_B
            assert row.bitrot_candidate_hash is None
            assert row.bitrot_stable_checks == 0
            # Baseline mtime is the one recorded WITH the candidate hash, not a
            # fresh stat (the file may have changed again since the check)
            assert row.last_modified == check_mtime
            assert row.mtime_baseline_utc is True
            # Detection record is permanent
            assert row.bitrot_detected_date == detected
            assert row.bitrot_details == details

    def test_accept_without_recorded_mtime_still_clears_flag(self, authenticated_client, app, db):
        with app.app_context():
            original_mtime = datetime(2026, 6, 1, 8, 0, 0)
            row = seed(db, '/m/rotten2.mkv', bitrot_suspected=True,
                       bitrot_candidate_hash=HASH_B, last_modified=original_mtime,
                       bitrot_details='{"x": 1}')

            response = authenticated_client.post('/api/bitrot/accept',
                                                 json={'file_ids': [row.id]})
            assert response.status_code == 200
            assert response.get_json()['accepted'] == 1

            db.session.refresh(row)
            assert row.bitrot_suspected is False
            assert row.file_hash == HASH_B
            # No recorded check mtime: stored mtime untouched and still
            # untrusted; the next hash-match integrity check re-baselines it
            assert row.last_modified == original_mtime
            assert row.mtime_baseline_utc is False

    def test_accept_skips_unflagged_files(self, authenticated_client, app, db):
        with app.app_context():
            row = seed(db, '/m/clean.mkv')

            response = authenticated_client.post('/api/bitrot/accept',
                                                 json={'file_ids': [row.id]})
            assert response.status_code == 200
            data = response.get_json()
            assert data['accepted'] == 0
            assert data['skipped'] == [row.id]

    def test_accept_rejects_bad_ids(self, authenticated_client, app, db):
        with app.app_context():
            response = authenticated_client.post('/api/bitrot/accept',
                                                 json={'file_ids': ['abc']})
            assert response.status_code == 400

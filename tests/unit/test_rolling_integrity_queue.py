"""Unit tests for the rolling integrity queue.

fetch_integrity_batch is the ordered, watermark-bounded queue fetch that
replaced the load-everything query in _run_file_changes_check: stalest files
first, rows stamped during the current run excluded, in-flight rows excluded
explicitly.
"""

from datetime import datetime, timedelta

from pixelprobe.models import ScanResult
from pixelprobe.services.maintenance_service import fetch_integrity_batch

# Naive UTC datetimes, matching what the DateTime column stores.
NOW = datetime(2026, 7, 1, 12, 0, 0)
WATERMARK = NOW + timedelta(hours=1)


def seed(db, path, checked=None, size=1000):
    row = ScanResult(
        file_path=path,
        file_size=size,
        file_type='video/mp4',
        scan_status='completed',
        file_hash='cafe' * 16,
        last_integrity_check_date=checked,
    )
    db.session.add(row)
    db.session.commit()
    return row.id


class TestQueueOrdering:

    def test_never_checked_files_come_first(self, db):
        checked = seed(db, '/m/checked.mkv', checked=NOW - timedelta(days=2))
        never = seed(db, '/m/never.mkv', checked=None)

        batch = fetch_integrity_batch(None, WATERMARK, set(), 10)

        assert [b['id'] for b in batch] == [never, checked]

    def test_stalest_first_with_id_tiebreak(self, db):
        old = seed(db, '/m/old.mkv', checked=NOW - timedelta(days=30))
        older = seed(db, '/m/older.mkv', checked=NOW - timedelta(days=60))
        tie_a = seed(db, '/m/tie_a.mkv', checked=NOW - timedelta(days=10))
        tie_b = seed(db, '/m/tie_b.mkv', checked=NOW - timedelta(days=10))

        batch = fetch_integrity_batch(None, WATERMARK, set(), 10)

        assert [b['id'] for b in batch] == [older, old, tie_a, tie_b]

    def test_smallest_files_first_within_batch(self, db):
        # Staleness decides WHICH rows are in the batch; size decides
        # dispatch order within it (small files backfill the wide slots).
        big = seed(db, '/m/big.mkv', checked=NOW - timedelta(days=9), size=5000)
        small = seed(db, '/m/small.mkv', checked=NOW - timedelta(days=1), size=10)

        batch = fetch_integrity_batch(None, WATERMARK, set(), 10)

        assert [b['id'] for b in batch] == [small, big]

    def test_budgeted_runs_dispatch_largest_first(self, db):
        # Under a time budget a huge file must start hashing early in the
        # window, not at the deadline.
        big = seed(db, '/m/big.mkv', checked=None, size=5000)
        small = seed(db, '/m/small.mkv', checked=None, size=10)

        batch = fetch_integrity_batch(None, WATERMARK, set(), 10, largest_first=True)

        assert [b['id'] for b in batch] == [big, small]


class TestQueueBounds:

    def test_watermark_excludes_rows_stamped_this_run(self, db):
        done = seed(db, '/m/done.mkv', checked=WATERMARK + timedelta(seconds=5))
        todo = seed(db, '/m/todo.mkv', checked=NOW)

        ids = [b['id'] for b in fetch_integrity_batch(None, WATERMARK, set(), 10)]

        assert todo in ids
        assert done not in ids

    def test_excluded_ids_are_skipped(self, db):
        in_flight = seed(db, '/m/inflight.mkv', checked=None)
        fresh = seed(db, '/m/fresh.mkv', checked=None)

        ids = [b['id'] for b in fetch_integrity_batch(None, WATERMARK, {in_flight}, 10)]

        assert ids == [fresh]

    def test_batch_size_limits_fetch(self, db):
        for i in range(5):
            seed(db, f'/m/f{i}.mkv', checked=NOW - timedelta(days=5 - i))

        batch = fetch_integrity_batch(None, WATERMARK, set(), 2)

        assert len(batch) == 2

    def test_file_paths_scopes_the_queue(self, db):
        seed(db, '/m/other.mkv', checked=None)
        wanted = seed(db, '/m/wanted.mkv', checked=None)

        ids = [b['id'] for b in fetch_integrity_batch(['/m/wanted.mkv'], WATERMARK, set(), 10)]

        assert ids == [wanted]


class TestRollingSweep:

    def test_stamped_batches_are_disjoint_and_cover_everything(self, db):
        # Two budgeted runs must process disjoint slices; a full sweep is the
        # union of successive batches as processed files get stamped.
        all_ids = {seed(db, f'/m/s{i}.mkv', checked=NOW - timedelta(days=i)) for i in range(10)}

        processed = []
        for _ in range(4):
            batch = fetch_integrity_batch(None, WATERMARK, set(), 3)
            for entry in batch:
                row = db.session.get(ScanResult, entry['id'])
                row.last_integrity_check_date = WATERMARK + timedelta(seconds=1)
            db.session.commit()
            processed.append({entry['id'] for entry in batch})

        for i in range(len(processed)):
            for j in range(i + 1, len(processed)):
                assert not (processed[i] & processed[j]), 'batches overlap'
        assert set().union(*processed) == all_ids
        assert fetch_integrity_batch(None, WATERMARK, set(), 3) == []

    def test_dict_shape_matches_dispatch_contract(self, db):
        seed(db, '/m/shape.mkv', checked=None, size=123)

        batch = fetch_integrity_batch(None, WATERMARK, set(), 1)

        assert set(batch[0]) == {'id', 'file_path', 'file_hash', 'file_size', 'last_modified',
                                 'mtime_baseline_utc', 'bitrot_suspected', 'bitrot_candidate_hash'}

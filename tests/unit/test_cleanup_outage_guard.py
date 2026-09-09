"""Unit tests for the orphan cleanup's per-file confirmation.

A file reading as missing is not proof it was deleted: an unreachable mount says
exactly the same thing, and deleting those records cannot be undone. What
settles it is reading the file's own directory and finding other files in it.
An empty directory settles nothing, because an unmounted mountpoint reads as an
empty directory, and neither does a directory that is gone: a folder the
operator deleted and a folder inside a mount that came down are the same ENOENT.
Those records are kept for the operator to confirm.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from pixelprobe.models import CleanupState, ScanReport, ScanResult
from pixelprobe.services.maintenance_service import MaintenanceService
from pixelprobe.utils.helpers import PATH_UNKNOWN


def _service():
    return MaintenanceService(':memory:')


@pytest.fixture
def clean_db(app, db):
    with app.app_context():
        ScanResult.query.delete()
        db.session.commit()
        yield db
        ScanResult.query.delete()
        db.session.commit()


def _record(db, paths):
    """Record scan results for the given paths."""
    for path in paths:
        db.session.add(ScanResult(file_path=path, scan_status='completed'))
    db.session.commit()


def _entries(*paths):
    return [{'file_id': i, 'file_path': str(p)} for i, p in enumerate(paths, start=1)]


class TestConfirmOrphans:

    def test_an_occupied_folder_settles_it(self, tmp_path):
        """The reported case: files deleted, their folder still holding others."""
        folder = tmp_path / 'movies'
        folder.mkdir()
        (folder / 'kept.mkv').write_text('x')

        deletable, unconfirmed, returned, reason = _service()._confirm_orphans(
            _entries(*[folder / f'gone{i}.mkv' for i in range(331)]))

        assert len(deletable) == 331
        assert unconfirmed == [] and returned == []
        assert reason == ''

    def test_an_empty_folder_is_kept_because_a_dead_mount_reads_the_same(self, tmp_path):
        """An unmounted mountpoint reverts to an empty directory that stats
        perfectly well, so a stat is not evidence that the file was deleted."""
        mountpoint = tmp_path / 'movies'
        mountpoint.mkdir()

        deletable, unconfirmed, _returned, reason = _service()._confirm_orphans(
            _entries(*[mountpoint / f'film{i}.mkv' for i in range(500)]))

        assert deletable == []
        assert len(unconfirmed) == 500
        assert 'empty or unreadable' in reason

    def test_the_library_above_a_vanished_folder_answers_for_it(self, tmp_path, clean_db):
        """One film per folder: deleting it empties or removes the folder, so
        the evidence has to be the rest of the library, still where it was."""
        movies = tmp_path / 'movies'
        (movies / 'Arrival (2016)').mkdir(parents=True)
        (movies / 'Arrival (2016)' / 'Arrival.mkv').write_text('x')
        _record(clean_db, [str(movies / 'Arrival (2016)' / 'Arrival.mkv')])
        emptied = movies / 'In Her Shoes (2005)'
        emptied.mkdir()

        deletable, unconfirmed, _returned, _reason = _service()._confirm_orphans(
            _entries(emptied / 'In Her Shoes.mkv', movies / 'Gone (2001)' / 'Gone.mkv'))

        assert len(deletable) == 2
        assert unconfirmed == []

    def test_leftovers_on_an_unmounted_mountpoint_do_not_answer(self, tmp_path, clean_db):
        """A listing alone would pass here: files written to a mountpoint while
        it was unmounted list just as well as the library does."""
        movies = tmp_path / 'movies'
        (movies / 'stray').mkdir(parents=True)
        (movies / 'stray' / 'leftover.txt').write_text('x')

        deletable, unconfirmed, _returned, _reason = _service()._confirm_orphans(
            _entries(*[movies / f'Film {i} (2001)' / f'film{i}.mkv' for i in range(20)]))

        assert deletable == []
        assert len(unconfirmed) == 20

    def test_a_missing_folder_is_kept(self, tmp_path):
        gone_folder = tmp_path / 'movies' / 'Show'

        deletable, unconfirmed, _returned, reason = _service()._confirm_orphans(
            _entries(*[gone_folder / f'ep{i}.mkv' for i in range(200)]))

        assert deletable == []
        assert len(unconfirmed) == 200
        assert str(gone_folder) in reason

    def test_the_operator_can_confirm_them(self, tmp_path):
        gone_folder = tmp_path / 'movies' / 'Show'

        deletable, unconfirmed, _returned, _reason = _service()._confirm_orphans(
            _entries(*[gone_folder / f'ep{i}.mkv' for i in range(200)]),
            trust_unreadable_dirs=True)

        assert len(deletable) == 200
        assert unconfirmed == []

    def test_a_file_that_came_back_is_left_alone(self, tmp_path):
        """Phase 2 can be hours old. A mount that dropped during it and has
        since returned must not have its files deleted from the database."""
        folder = tmp_path / 'movies'
        folder.mkdir()
        (folder / 'back.mkv').write_text('x')

        deletable, _unconfirmed, returned, _reason = _service()._confirm_orphans(
            _entries(folder / 'back.mkv', folder / 'really-gone.mkv'))

        assert [e['file_path'] for e in deletable] == [str(folder / 'really-gone.mkv')]
        assert [e['file_path'] for e in returned] == [str(folder / 'back.mkv')]

    def test_an_unreadable_file_is_not_treated_as_deleted(self, tmp_path):
        """A half-restored mount answers EIO, which is not "not there"."""
        folder = tmp_path / 'movies'
        folder.mkdir()
        (folder / 'kept.mkv').write_text('x')

        with patch.object(MaintenanceService, '_classify', return_value=PATH_UNKNOWN):
            deletable, unconfirmed, returned, _reason = _service()._confirm_orphans(
                _entries(folder / 'unreadable.mkv'))

        assert deletable == [] and unconfirmed == []
        assert len(returned) == 1

    def test_one_dead_tree_does_not_hold_up_a_healthy_one(self, tmp_path):
        """The verdict is per file, so a mount coming down under one path never
        blocks - or licenses - deleting records under another."""
        movies = tmp_path / 'movies'
        movies.mkdir()
        (movies / 'alive.mkv').write_text('x')

        deletable, unconfirmed, _returned, _reason = _service()._confirm_orphans(
            _entries(movies / 'deleted.mkv', tmp_path / 'tv' / 'Show' / 'ep1.mkv'))

        assert [e['file_path'] for e in deletable] == [str(movies / 'deleted.mkv')]
        assert [e['file_path'] for e in unconfirmed] == [str(tmp_path / 'tv' / 'Show' / 'ep1.mkv')]

    def test_the_reason_stays_short_enough_to_store(self, tmp_path):
        """progress_message and error_message are VARCHAR(1000)."""
        entries = _entries(*[tmp_path / 'gone' / f'dir{i}' / 'f.mkv' for i in range(400)])

        _deletable, unconfirmed, _returned, reason = _service()._confirm_orphans(entries)

        assert len(unconfirmed) == 400
        assert len(reason) < 900
        assert 'and 397 more' in reason


class TestOutageNote:

    def test_keeping_most_of_a_run_is_called_out(self):
        assert 'storage outage' in _service()._outage_note(331, 345)

    def test_keeping_a_handful_is_not(self):
        assert _service()._outage_note(20, 345) == ''

    def test_nothing_kept_is_not(self):
        assert _service()._outage_note(0, 345) == ''


class TestTrustCeiling:

    def test_a_confirmation_only_covers_what_the_last_run_kept(self, app, db):
        with app.app_context():
            CleanupState.query.delete()
            db.session.commit()
            previous = CleanupState(cleanup_id='previous', is_active=False, phase='complete',
                                    records_kept=331)
            current = CleanupState(cleanup_id='current', is_active=True, phase='checking_files')
            db.session.add_all([previous, current])
            db.session.commit()

            assert _service()._trust_ceiling(current.id) == 331
            CleanupState.query.delete()
            db.session.commit()

    def test_no_previous_run_confirms_nothing(self, app, db):
        with app.app_context():
            CleanupState.query.delete()
            db.session.commit()
            current = CleanupState(cleanup_id='only', is_active=True, phase='checking_files')
            db.session.add(current)
            db.session.commit()

            assert _service()._trust_ceiling(current.id) == 0
            CleanupState.query.delete()
            db.session.commit()


class TestReports:
    """A run that deleted nothing has to leave a record, or the only evidence
    the operator gets is a count that did not move."""

    def _state(self, db, **kwargs):
        record = CleanupState(
            cleanup_id='run', is_active=False,
            start_time=datetime.now(timezone.utc) - timedelta(minutes=3),
            end_time=datetime.now(timezone.utc), **kwargs)
        db.session.add(record)
        db.session.commit()
        return record

    def test_aborted_run_records_the_reason_and_no_deletions(self, app, db):
        with app.app_context():
            record = self._state(
                db, phase='error', phase_number=2, total_files=345, files_processed=345,
                orphaned_found=331, records_kept=331,
                error_message='Aborted: 331 records are missing a readable directory, more '
                              'than the 0 you confirmed. No entries were deleted.')

            report = _service()._create_cleanup_report(record, ['/movies/gone.mkv'])

            assert report.status == 'error'
            assert report.orphaned_records_found == 331
            assert report.orphaned_records_deleted == 0
            assert 'No entries were deleted' in report.error_message
            assert ScanReport.query.filter_by(report_id=report.report_id).first() is not None

    def test_stopped_phase_three_records_what_it_deleted(self, app, db):
        with app.app_context():
            record = self._state(db, phase='cancelled', phase_number=3, total_files=331,
                                 files_processed=50, orphaned_found=331)

            report = _service()._create_cleanup_report(record, [])

            assert report.status == 'cancelled'
            assert report.orphaned_records_deleted == 50

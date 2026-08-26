"""Tests for scoped mark-as-good overrides."""

from pixelprobe.models import db, ScanResult
from pixelprobe.utils.overrides import (
    classify_findings, encode_verdict, override_still_applies)


class TestClassifyFindings:
    def test_freeze_warning_classifies_as_freeze(self):
        assert classify_findings(None, 'Video freeze warning: 2 event(s)') == ('freeze',)

    def test_incomplete_file_classifies_as_incomplete(self):
        assert classify_findings('Incomplete file: 24 unwritten region(s)', None) == ('incomplete',)

    def test_corroborated_verdicts_have_their_own_classes(self):
        assert classify_findings('Missing content: 1 frozen segment(s)', None) == ('missing-content',)
        assert classify_findings('Decode failure: 2 frozen segment(s)', None) == ('decode',)

    def test_unrecognised_findings_map_to_other(self):
        assert classify_findings('Something brand new happened', None) == ('other',)

    def test_multiple_classes_collect(self):
        got = classify_findings('Incomplete file: x', 'Video freeze warning: y')
        assert got == ('freeze', 'incomplete')

    def test_empty_details_classify_as_nothing(self):
        assert classify_findings(None, '') == ()
        assert encode_verdict(()) is None


class TestOverrideStillApplies:
    HASH = 'a' * 64

    def test_same_content_same_class_holds(self):
        assert override_still_applies(
            self.HASH, 'freeze', self.HASH,
            None, 'Video freeze warning: 1 event(s)') is True

    def test_changed_content_lapses(self):
        assert override_still_applies(
            self.HASH, 'freeze', 'b' * 64,
            None, 'Video freeze warning: 1 event(s)') is False

    def test_different_finding_class_lapses(self):
        """Excusing a freeze must not hide a later incomplete-file verdict"""
        assert override_still_applies(
            self.HASH, 'freeze', self.HASH,
            'Incomplete file: 3 unwritten region(s)', None) is False

    def test_subset_of_excused_classes_holds(self):
        assert override_still_applies(
            self.HASH, 'freeze,temporal', self.HASH,
            None, 'Video freeze warning: 1 event(s)') is True

    def test_legacy_null_verdict_excuses_everything(self):
        assert override_still_applies(
            self.HASH, None, self.HASH,
            'Incomplete file: x', 'Video freeze warning: y') is True

    def test_legacy_null_hash_skips_content_check(self):
        assert override_still_applies(
            None, 'freeze', 'b' * 64,
            None, 'Video freeze warning: 1 event(s)') is True

    def test_mark_on_a_healthy_file_excuses_nothing(self):
        """The 'none' sentinel a healthy-file mark stores hides no later finding"""
        assert override_still_applies(
            self.HASH, 'none', self.HASH,
            None, 'Video freeze warning: 1 event(s)') is False
        assert override_still_applies(
            self.HASH, 'none', self.HASH, None, None) is True

    def test_unknown_new_finding_never_hides(self):
        """A finding class that did not exist at mark time must surface"""
        assert override_still_applies(
            self.HASH, 'freeze', self.HASH,
            'Some future verdict text', None) is False


class TestMarkAsGoodRecordsScope:
    def test_mark_records_hash_date_and_verdict(self, authenticated_client, app):
        with app.app_context():
            db.create_all()
            row = ScanResult(
                file_path='/test/scoped.mkv', file_size=1000,
                file_type='video/x-matroska', is_corrupted=False,
                has_warnings=True,
                warning_details='Video freeze warning: 1 event(s)',
                file_hash='c' * 64, scan_status='completed')
            db.session.add(row)
            db.session.commit()
            row_id = row.id

        resp = authenticated_client.post('/api/mark-as-good',
                                         json={'file_ids': [row_id]})
        assert resp.status_code == 200

        with app.app_context():
            row = db.session.get(ScanResult, row_id)
            assert row.marked_as_good is True
            assert row.marked_good_hash == 'c' * 64
            assert row.marked_good_verdict == 'freeze'
            assert row.marked_good_date is not None
            db.session.delete(row)
            db.session.commit()

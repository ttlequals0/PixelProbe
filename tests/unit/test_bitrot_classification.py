"""Unit tests for bitrot classification.

classify_file_change is the pure classification matrix; MaintenanceService
_apply_change_classification is the Phase 3 state machine (flagging,
auto-expire, self-heal); dispatch_event is the notification rule evaluator.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from pixelprobe.utils.integrity import (
    classify_file_change,
    apply_scan_baseline,
    adopt_bitrot_baseline,
    MTIME_EPSILON_SECONDS,
)
from pixelprobe.models import ScanResult, NotificationProvider, NotificationRule
from pixelprobe.services import notification_service as ns
from pixelprobe.services import maintenance_service as ms
from pixelprobe.services.maintenance_service import (
    MaintenanceService,
    BITROT_STABLE_CHECKS_TO_EXPIRE,
)

NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
HASH_A = 'a' * 64
HASH_B = 'b' * 64
HASH_C = 'c' * 64


class TestClassifyFileChange:
    """Every row of the classification matrix."""

    def test_hash_match_is_unchanged(self):
        assert classify_file_change(HASH_A, HASH_A, NOW.isoformat(), NOW) == ('unchanged', False)

    def test_missing_stored_hash_is_no_hash(self):
        assert classify_file_change(None, HASH_A, None, NOW) == ('no_hash', True)

    def test_mismatch_with_changed_mtime_is_modified(self):
        stored = (NOW - timedelta(hours=5)).isoformat()
        assert classify_file_change(HASH_A, HASH_B, stored, NOW,
                                    mtime_trusted=True) == ('modified', True)

    def test_mismatch_with_unchanged_mtime_is_bitrot(self):
        assert classify_file_change(HASH_A, HASH_B, NOW.isoformat(), NOW,
                                    mtime_trusted=True) == ('bitrot_suspected', True)

    def test_untrusted_baseline_never_classifies_bitrot(self):
        # Pre-upgrade naive-local baselines fall back to 'modified', which
        # re-baselines in UTC. Identical mtimes must NOT flag.
        assert classify_file_change(HASH_A, HASH_B, NOW.isoformat(), NOW,
                                    mtime_trusted=False) == ('modified', True)

    def test_unparseable_stored_mtime_is_modified(self):
        assert classify_file_change(HASH_A, HASH_B, 'not-a-date', NOW,
                                    mtime_trusted=True) == ('modified', True)

    def test_epsilon_boundary_inside_is_bitrot(self):
        stored = (NOW - timedelta(seconds=MTIME_EPSILON_SECONDS)).isoformat()
        assert classify_file_change(HASH_A, HASH_B, stored, NOW,
                                    mtime_trusted=True) == ('bitrot_suspected', True)

    def test_epsilon_boundary_outside_is_modified(self):
        stored = (NOW - timedelta(seconds=MTIME_EPSILON_SECONDS + 1)).isoformat()
        assert classify_file_change(HASH_A, HASH_B, stored, NOW,
                                    mtime_trusted=True) == ('modified', True)

    def test_naive_stored_iso_treated_as_utc(self):
        naive_iso = NOW.replace(tzinfo=None).isoformat()
        assert classify_file_change(HASH_A, HASH_B, naive_iso, NOW,
                                    mtime_trusted=True) == ('bitrot_suspected', True)


class TestClassifyAlreadyFlagged:
    """The auto-expire state machine inputs."""

    def test_current_matches_candidate_is_stable(self):
        result = classify_file_change(HASH_A, HASH_B, NOW.isoformat(), NOW,
                                      bitrot_suspected=True, bitrot_candidate_hash=HASH_B)
        assert result == ('bitrot_stable', True)

    def test_current_matches_original_baseline_is_self_healed(self):
        result = classify_file_change(HASH_A, HASH_A, NOW.isoformat(), NOW,
                                      bitrot_suspected=True, bitrot_candidate_hash=HASH_B)
        assert result == ('bitrot_self_healed', True)

    def test_third_hash_is_active_rot(self):
        result = classify_file_change(HASH_A, HASH_C, NOW.isoformat(), NOW,
                                      bitrot_suspected=True, bitrot_candidate_hash=HASH_B)
        assert result == ('bitrot_active', True)


def seed_row(db, path='/m/f.mkv', **kwargs):
    defaults = dict(
        file_path=path,
        file_size=1000,
        file_type='video/mp4',
        scan_status='completed',
        file_hash=HASH_A,
        is_corrupted=False,
    )
    defaults.update(kwargs)
    row = ScanResult(**defaults)
    db.session.add(row)
    db.session.commit()
    return row


def change_info(change_type, **kwargs):
    info = {
        'file_id': 1,
        'file_path': '/m/f.mkv',
        'change_type': change_type,
        'stored_hash': HASH_A,
        'current_hash': HASH_B,
        'stored_modified': NOW.isoformat(),
        'current_modified': NOW.isoformat(),
        'stored_size': 1000,
        'current_size': 1000,
    }
    info.update(kwargs)
    return info


class TestApplyChangeClassification:

    def _service(self):
        service = MaintenanceService(':memory:')
        return service

    def test_new_detection_flags_file(self, app, db):
        row = seed_row(db)
        service = self._service()
        category = service._apply_change_classification(row, change_info('bitrot_suspected'))

        assert category == 'bitrot_new'
        assert row.bitrot_suspected is True
        assert row.bitrot_detected_date is not None
        assert row.bitrot_candidate_hash == HASH_B
        assert row.bitrot_stable_checks == 0
        assert row.scan_status == 'pending'
        details = json.loads(row.bitrot_details)
        assert details['stored_hash'] == HASH_A
        assert details['current_hash'] == HASH_B

    def test_first_detection_date_is_permanent(self, app, db):
        detected = NOW.replace(tzinfo=None)
        row = seed_row(db, bitrot_detected_date=detected)
        service = self._service()
        service._apply_change_classification(row, change_info('bitrot_suspected'))

        assert row.bitrot_detected_date == detected  # re-detection keeps first date

    def test_rescan_does_not_clear_flag_or_adopt_hash(self, app, db):
        # The anti-laundering guard lives in apply_scan_baseline; here we assert
        # the Phase 3 detection leaves the stored baseline untouched.
        row = seed_row(db)
        service = self._service()
        service._apply_change_classification(row, change_info('bitrot_suspected'))

        assert row.file_hash == HASH_A  # baseline NOT overwritten
        assert row.bitrot_suspected is True

    def test_active_rot_resets_counter(self, app, db):
        row = seed_row(db, bitrot_suspected=True, bitrot_candidate_hash=HASH_B,
                       bitrot_stable_checks=1)
        service = self._service()
        category = service._apply_change_classification(
            row, change_info('bitrot_active', current_hash=HASH_C))

        assert category == 'bitrot_active'
        assert row.bitrot_candidate_hash == HASH_C
        assert row.bitrot_stable_checks == 0

    def test_stale_bitrot_result_on_unflagged_row_is_ignored(self, app, db):
        # The flag can be cleared (manual accept) between task dispatch and
        # Phase 3 apply; the queued bitrot result must not resurrect state.
        row = seed_row(db, bitrot_suspected=False, bitrot_candidate_hash=None,
                       bitrot_stable_checks=0)
        service = self._service()
        for stale_type in ('bitrot_stable', 'bitrot_self_healed', 'bitrot_active'):
            category = service._apply_change_classification(
                row, change_info(stale_type, current_hash=HASH_C))
            assert category == 'other'
            assert row.bitrot_suspected is False
            assert row.bitrot_candidate_hash is None
            assert row.file_hash == HASH_A

    def test_stable_check_increments_without_expiry_below_threshold(self, app, db):
        row = seed_row(db, bitrot_suspected=True, bitrot_candidate_hash=HASH_B,
                       bitrot_stable_checks=0)
        service = self._service()
        category = service._apply_change_classification(
            row, change_info('bitrot_stable', current_hash=HASH_B))

        assert category == 'bitrot_stable'
        assert row.bitrot_stable_checks == 1
        assert row.bitrot_suspected is True
        assert row.file_hash == HASH_A

    def test_auto_expire_adopts_candidate_and_keeps_record(self, app, db):
        detected = NOW.replace(tzinfo=None)
        row = seed_row(db, bitrot_suspected=True, bitrot_candidate_hash=HASH_B,
                       bitrot_stable_checks=BITROT_STABLE_CHECKS_TO_EXPIRE - 1,
                       bitrot_detected_date=detected, bitrot_details='{"x": 1}',
                       scan_status='completed', is_corrupted=False)
        service = self._service()
        category = service._apply_change_classification(
            row, change_info('bitrot_stable', current_hash=HASH_B))

        assert category == 'bitrot_expired'
        assert row.bitrot_suspected is False
        assert row.file_hash == HASH_B  # candidate adopted as baseline
        assert row.mtime_baseline_utc is True
        assert row.bitrot_candidate_hash is None
        assert row.bitrot_stable_checks == 0
        # Detection record is permanent
        assert row.bitrot_detected_date == detected
        assert row.bitrot_details == '{"x": 1}'

    def test_no_expiry_while_rescan_dirty_or_pending(self, app, db):
        for status, corrupted in (('pending', False), ('completed', True)):
            row = seed_row(db, path=f'/m/{status}_{corrupted}.mkv',
                           bitrot_suspected=True, bitrot_candidate_hash=HASH_B,
                           bitrot_stable_checks=BITROT_STABLE_CHECKS_TO_EXPIRE - 1,
                           scan_status=status, is_corrupted=corrupted)
            service = self._service()
            category = service._apply_change_classification(
                row, change_info('bitrot_stable', current_hash=HASH_B))

            assert category == 'bitrot_stable'
            assert row.bitrot_suspected is True
            assert row.file_hash == HASH_A

    def test_self_healed_clears_flag_keeps_record(self, app, db):
        detected = NOW.replace(tzinfo=None)
        row = seed_row(db, bitrot_suspected=True, bitrot_candidate_hash=HASH_B,
                       bitrot_stable_checks=1, bitrot_detected_date=detected)
        service = self._service()
        category = service._apply_change_classification(
            row, change_info('bitrot_self_healed', current_hash=HASH_A))

        assert category == 'bitrot_self_healed'
        assert row.bitrot_suspected is False
        assert row.bitrot_candidate_hash is None
        assert row.file_hash == HASH_A
        assert row.bitrot_detected_date == detected

    def test_deleted_records_absence_without_row_deletion(self, app, db):
        row = seed_row(db)
        service = self._service()
        category = service._apply_change_classification(
            row, change_info('deleted', current_hash=None))

        assert category == 'deleted'
        assert row.file_exists is False
        # Row deletion is exclusively orphan cleanup's job
        assert db.session.get(ScanResult, row.id) is not None
        # A gone file is not queued for rescan
        assert row.scan_status == 'completed'

    def test_modified_marks_pending(self, app, db):
        row = seed_row(db)
        service = self._service()
        category = service._apply_change_classification(row, change_info('modified'))

        assert category == 'modified'
        assert row.scan_status == 'pending'
        assert row.bitrot_suspected is False


class TestDispatchEvent:

    def _provider_and_rule(self, db, provider_active=True, rule_active=True,
                           event_type='bitrot_suspected'):
        provider = NotificationProvider(
            name='test-webhook',
            provider_type='webhook',
            is_active=provider_active,
            configuration={'webhook_url': 'https://example.com/hook'},
        )
        db.session.add(provider)
        db.session.commit()
        rule = NotificationRule(
            provider_id=provider.id,
            event_type=event_type,
            is_active=rule_active,
            priority='high',
        )
        db.session.add(rule)
        db.session.commit()
        return provider, rule

    def test_dispatches_to_matching_active_rule(self, app, db):
        provider, _ = self._provider_and_rule(db)

        with patch.object(ns.NotificationService, 'send_notification',
                          return_value=(True, None)) as send:
            sent = ns.dispatch_event('bitrot_suspected', 'title', 'message')

        assert sent == 1
        send.assert_called_once()
        assert send.call_args.kwargs['priority'] == 'high'
        assert provider.last_notification_status == 'success'

    def test_default_rule_priority_defers_to_event_priority(self, app, db):
        # NotificationRule.priority defaults to 'normal' (NOT NULL); a rule
        # left at the default must not downgrade a high-priority event.
        self._provider_and_rule(db)
        rule = NotificationRule.query.first()
        rule.priority = 'normal'
        db.session.commit()

        with patch.object(ns.NotificationService, 'send_notification',
                          return_value=(True, None)) as send:
            ns.dispatch_event('bitrot_suspected', 't', 'm', priority='high')

        assert send.call_args.kwargs['priority'] == 'high'

    def test_skips_inactive_rule_and_provider(self, app, db):
        self._provider_and_rule(db, rule_active=False)

        with patch.object(ns.NotificationService, 'send_notification') as send:
            assert ns.dispatch_event('bitrot_suspected', 't', 'm') == 0
        send.assert_not_called()

    def test_skips_non_matching_event_type(self, app, db):
        self._provider_and_rule(db, event_type='scan_complete')

        with patch.object(ns.NotificationService, 'send_notification') as send:
            assert ns.dispatch_event('bitrot_suspected', 't', 'm') == 0
        send.assert_not_called()

    def test_provider_failure_recorded_not_raised(self, app, db):
        provider, _ = self._provider_and_rule(db)

        with patch.object(ns.NotificationService, 'send_notification',
                          return_value=(False, 'boom')):
            assert ns.dispatch_event('bitrot_suspected', 't', 'm') == 0
        assert provider.last_notification_status == 'failure'


class TestBaselineHelpers:

    def test_apply_scan_baseline_writes_and_trusts(self, app, db):
        row = seed_row(db)
        mtime = NOW.replace(tzinfo=None)

        assert apply_scan_baseline(row, HASH_B, mtime) is True
        assert row.file_hash == HASH_B
        assert row.last_modified == mtime
        assert row.mtime_baseline_utc is True

    def test_apply_scan_baseline_refuses_flagged_rows(self, app, db):
        row = seed_row(db, bitrot_suspected=True, last_modified=NOW.replace(tzinfo=None))

        assert apply_scan_baseline(row, HASH_B, NOW.replace(tzinfo=None) + timedelta(days=1)) is False
        assert row.file_hash == HASH_A
        assert row.last_modified == NOW.replace(tzinfo=None)
        assert row.mtime_baseline_utc is False

    def test_apply_scan_baseline_without_mtime_keeps_trust_state(self, app, db):
        # Error-path scans carry last_modified=None; the stored mtime and its
        # trust flag must survive so classification is not poisoned.
        mtime = NOW.replace(tzinfo=None)
        row = seed_row(db, last_modified=mtime, mtime_baseline_utc=True)

        assert apply_scan_baseline(row, HASH_B, None) is True
        assert row.file_hash == HASH_B
        assert row.last_modified == mtime
        assert row.mtime_baseline_utc is True

    def test_adopt_bitrot_baseline_clears_flag_keeps_record(self, app, db):
        detected = NOW.replace(tzinfo=None)
        mtime = detected + timedelta(days=1)
        row = seed_row(db, bitrot_suspected=True, bitrot_candidate_hash=HASH_B,
                       bitrot_stable_checks=2, bitrot_detected_date=detected,
                       bitrot_details='{"x": 1}')

        adopt_bitrot_baseline(row, mtime)

        assert row.file_hash == HASH_B
        assert row.last_modified == mtime
        assert row.mtime_baseline_utc is True
        assert row.bitrot_suspected is False
        assert row.bitrot_candidate_hash is None
        assert row.bitrot_stable_checks == 0
        assert row.bitrot_detected_date == detected
        assert row.bitrot_details == '{"x": 1}'


class TestBitrotSummaryNotification:

    def test_summary_sends_single_aggregated_event(self, app, db):
        service = MaintenanceService(':memory:')
        paths = [f'/m/f{i}.mkv' for i in range(15)]

        with patch.object(ms, 'dispatch_event') as dispatch:
            service._notify_bitrot_summary(paths)

        dispatch.assert_called_once()
        args, kwargs = dispatch.call_args
        assert args[0] == 'bitrot_suspected'
        assert '15' in args[1]
        assert kwargs['priority'] == 'high'
        assert kwargs['additional_data']['count'] == 15

    def test_summary_with_no_flagged_files_sends_nothing(self, app, db):
        service = MaintenanceService(':memory:')
        with patch.object(ms, 'dispatch_event') as dispatch:
            service._notify_bitrot_summary([])
        dispatch.assert_not_called()

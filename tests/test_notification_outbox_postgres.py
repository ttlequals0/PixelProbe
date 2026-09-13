"""PostgreSQL regressions for the durable scan notification outbox."""

import os
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from flask import Flask
from sqlalchemy import create_engine, text

from pixelprobe.models import (
    db,
    NotificationProvider,
    NotificationRule,
    ScanNotificationDelivery,
    ScanNotificationOutbox,
)

from pixelprobe.services.notification_outbox import (
    claim_notification_targets,
    deliver_notification_outbox,
    finish_notification_outbox,
    record_notification_target,
    renew_notification_target,
)
from pixelprobe.services.notification_service import snapshot_scan_notification_outbox

POSTGRES_URI = os.environ.get('PIXELPROBE_TEST_POSTGRES_URI')


@pytest.fixture
def notification_app():
    schema = f'notification_{uuid4().hex[:12]}'
    engine = create_engine(POSTGRES_URI)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=POSTGRES_URI,
        SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'options': f'-csearch_path={schema}'}},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()


def _outbox_with_rules(count=1, conditions=None):
    outbox = ScanNotificationOutbox(scan_id=uuid4().hex, event='scan_completed')
    db.session.add(outbox)
    for index in range(count):
        provider = NotificationProvider(
            name=f'provider-{index}', provider_type='webhook', is_active=True,
            configuration={'webhook_url': 'http://127.0.0.1/test'},
        )
        db.session.add(provider)
        db.session.flush()
        db.session.add(NotificationRule(
            provider_id=provider.id, event_type='scan_completed', is_active=True,
            conditions=conditions,
        ))
    db.session.commit()
    return outbox.id


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_concurrent_workers_claim_one_notification_target(notification_app):
    with notification_app.app_context():
        outbox_id = _outbox_with_rules()
    gate = Barrier(2)

    def claim():
        with notification_app.app_context():
            gate.wait()
            return claim_notification_targets(outbox_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: claim(), range(2)))

    claims = [claimed for _, claimed in outcomes]
    assert sum(len(claimed) for claimed in claims) == 1
    assert {state for state, _ in outcomes} == {'PROCESSING', 'IN_FLIGHT'}


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_active_target_lease_is_not_reclaimed(notification_app):
    with notification_app.app_context():
        outbox_id = _outbox_with_rules()
        assert claim_notification_targets(outbox_id)[0] == 'PROCESSING'
        assert claim_notification_targets(outbox_id) == ('IN_FLIGHT', [])
        target = ScanNotificationDelivery.query.one()
        assert target.attempts == 1
        assert target.status == 'processing'
        outbox = db.session.get(ScanNotificationOutbox, outbox_id)
        outbox.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()
        assert claim_notification_targets(outbox_id) == ('IN_FLIGHT', [])
        assert db.session.get(ScanNotificationOutbox, outbox_id).status == 'processing'


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_expired_target_lease_reclaims_only_that_target(notification_app):
    with notification_app.app_context():
        outbox_id = _outbox_with_rules()
        _, claimed = claim_notification_targets(outbox_id)
        target_id, _ = claimed[0]
        expired = datetime.now(timezone.utc) - timedelta(seconds=1)
        target = db.session.get(ScanNotificationDelivery, target_id)
        outbox = db.session.get(ScanNotificationOutbox, outbox_id)
        target.lease_expires_at = expired
        outbox.lease_expires_at = expired
        db.session.commit()

        state, recovered = claim_notification_targets(outbox_id)
        assert state == 'PROCESSING'
        assert [item[0] for item in recovered] == [target_id]
        assert db.session.get(ScanNotificationDelivery, target_id).attempts == 2


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_unmet_conditions_are_terminal_skips_not_retries(notification_app):
    with notification_app.app_context():
        outbox_id = _outbox_with_rules(
            conditions={'corrupted_count': '>0'},
        )
        assert claim_notification_targets(outbox_id) == ('DELIVERED', [])
        target = ScanNotificationDelivery.query.one()
        outbox = db.session.get(ScanNotificationOutbox, outbox_id)
        assert target.status == 'skipped'
        assert target.skip_reason == 'conditions_not_met'
        assert target.conditions == {'corrupted_count': '>0'}
        assert outbox.status == 'delivered'
        assert outbox.terminal_reason == 'no_eligible_targets'


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_delivery_runner_records_successful_provider_ack_on_its_target(notification_app):
    with notification_app.app_context():
        outbox_id = _outbox_with_rules()
        with patch('pixelprobe.services.notification_service.deliver_notification_target',
                   return_value=(True, None)) as send:
            result = deliver_notification_outbox(outbox_id)
        assert result['status'] == 'DELIVERED'
        assert send.call_count == 1
        target = ScanNotificationDelivery.query.one()
        assert target.status == 'delivered'


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_event_time_target_snapshot_survives_later_rule_and_provider_edits(notification_app):
    with notification_app.app_context():
        outbox_id = _outbox_with_rules()
        outbox = db.session.get(ScanNotificationOutbox, outbox_id)
        snapshot_scan_notification_outbox(outbox)
        db.session.commit()
        target = ScanNotificationDelivery.query.one()
        provider = db.session.get(NotificationProvider, target.provider_id)
        rule = db.session.get(NotificationRule, target.rule_id)
        provider.configuration = {'webhook_url': 'http://127.0.0.1/changed'}
        provider.is_active = False
        rule.is_active = False
        db.session.commit()
        target = db.session.get(ScanNotificationDelivery, target.id)
        assert target.provider_config == {'webhook_url': 'http://127.0.0.1/test'}
        assert target.status == 'pending'


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_expired_exhausted_lease_is_terminal_and_never_reclaimed(notification_app):
    with notification_app.app_context():
        outbox_id = _outbox_with_rules()
        _, claimed = claim_notification_targets(outbox_id)
        target_id, _ = claimed[0]
        expired = datetime.now(timezone.utc) - timedelta(seconds=1)
        target = db.session.get(ScanNotificationDelivery, target_id)
        outbox = db.session.get(ScanNotificationOutbox, outbox_id)
        target.attempts = 5
        target.lease_expires_at = expired
        outbox.lease_expires_at = expired
        db.session.commit()
        assert claim_notification_targets(outbox_id) == ('FAILED', [])
        target = db.session.get(ScanNotificationDelivery, target_id)
        assert target.status == 'failed'
        assert target.outcome == 'lease_expired'


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_expired_target_is_not_sent_by_a_stale_serial_worker(notification_app):
    with notification_app.app_context():
        outbox_id = _outbox_with_rules()
        _, claimed = claim_notification_targets(outbox_id)
        target_id, token = claimed[0]
        target = db.session.get(ScanNotificationDelivery, target_id)
        target.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()
        assert renew_notification_target(target_id, token) is None


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_delivery_error_redacts_capability_url_before_persistence(notification_app):
    with notification_app.app_context():
        outbox_id = _outbox_with_rules()
        _, claimed = claim_notification_targets(outbox_id)
        target_id, token = claimed[0]
        assert record_notification_target(
            target_id, token, False,
            'POST https://hooks.example.test/secret-path?token=secret-token failed',
        )
        error = db.session.get(ScanNotificationDelivery, target_id).error_message
        assert 'secret-token' not in error
        assert 'secret-path' not in error
        assert '[redacted URL]' in error


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_partial_provider_failure_retries_only_failed_target(notification_app):
    with notification_app.app_context():
        outbox_id = _outbox_with_rules(count=2)
        _, claimed = claim_notification_targets(outbox_id)
        first, second = claimed
        assert record_notification_target(first[0], first[1], True)
        assert record_notification_target(second[0], second[1], False, 'provider unavailable')
        assert finish_notification_outbox(outbox_id) == 'RETRY'

        state, retry_claims = claim_notification_targets(outbox_id)
        assert state == 'PROCESSING'
        assert [target_id for target_id, _ in retry_claims] == [second[0]]
        assert db.session.get(ScanNotificationDelivery, first[0]).status == 'delivered'


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_provider_failure_stops_after_bounded_target_attempts(notification_app):
    with notification_app.app_context():
        outbox_id = _outbox_with_rules()
        for attempt in range(5):
            state, claimed = claim_notification_targets(outbox_id)
            assert state == 'PROCESSING'
            target_id, token = claimed[0]
            assert record_notification_target(target_id, token, False, 'provider unavailable')
            expected = 'FAILED' if attempt == 4 else 'RETRY'
            assert finish_notification_outbox(outbox_id) == expected
        target = ScanNotificationDelivery.query.one()
        outbox = db.session.get(ScanNotificationOutbox, outbox_id)
        assert target.attempts == 5
        assert target.outcome == 'failed'
        assert outbox.status == 'failed'
        assert outbox.terminal_reason == 'target_failures_exhausted'


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_worker_crash_after_provider_acceptance_recovers_with_at_least_once_delivery(notification_app):
    with notification_app.app_context():
        outbox_id = _outbox_with_rules()
        _, claimed = claim_notification_targets(outbox_id)
        target_id, _ = claimed[0]
        expired = datetime.now(timezone.utc) - timedelta(seconds=1)
        target = db.session.get(ScanNotificationDelivery, target_id)
        outbox = db.session.get(ScanNotificationOutbox, outbox_id)
        # Simulate a worker dying after the provider accepts the request, before ack.
        target.lease_expires_at = expired
        outbox.lease_expires_at = expired
        db.session.commit()

        _, recovered = claim_notification_targets(outbox_id)
        assert [target for target, _ in recovered] == [target_id]
        assert db.session.get(ScanNotificationDelivery, target_id).attempts == 2

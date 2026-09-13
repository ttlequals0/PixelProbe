"""PostgreSQL regressions for durable scheduled healthcheck completion pings."""

import os
import sys
from types import ModuleType
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from flask import Flask
from sqlalchemy import create_engine, text

from pixelprobe.models import (db, HealthcheckConfig, ScanNotificationDelivery,
                               ScanNotificationOutbox, ScanRunFile, ScanRunRoot,
                               ScanSchedule, ScanState)
from pixelprobe.services.notification_outbox import deliver_notification_outbox
from pixelprobe.services.scan_engine import finalize_scan


POSTGRES_URI = os.environ.get('PIXELPROBE_TEST_POSTGRES_URI')


@pytest.fixture
def healthcheck_app():
    schema = f'healthcheck_outbox_{uuid4().hex[:12]}'
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


def _block_broker_dispatch(monkeypatch):
    tasks = ModuleType('pixelprobe.tasks')
    tasks.deliver_scan_notification_outbox = Mock()
    tasks.deliver_scan_notification_outbox.apply_async.side_effect = RuntimeError('broker unavailable')
    monkeypatch.setitem(sys.modules, 'pixelprobe.tasks', tasks)


def _scheduled_run(success=True, config_active=True, send_success=True, send_failure=True):
    schedule = ScanSchedule(name='nightly', cron_expression='0 2 * * *', scan_paths='["/media"]')
    db.session.add(schedule)
    db.session.flush()
    config = HealthcheckConfig(
        schedule_id=schedule.id,
        healthcheck_url='https://healthcheck.example.test/ping-token',
        is_active=config_active,
        send_success_ping=send_success,
        send_failure_ping=send_failure,
        include_report_data=True,
    )
    scan_id = f'scheduled_{schedule.id}_20260912_010203'
    state = ScanState(scan_id=scan_id, is_active=True, phase='scanning', estimated_total=1)
    db.session.add_all([config, state])
    db.session.flush()
    db.session.add(ScanRunFile(
        scan_id=scan_id, file_path='/media/movie.mp4', status='completed', outcome='completed',
    ))
    db.session.add(ScanRunRoot(
        scan_id=scan_id, root_path='/media', status='completed' if success else 'unavailable',
        discovered_count=1 if success else 0,
    ))
    db.session.commit()
    return state.id, config.id, scan_id


def _healthcheck_target(scan_id):
    outbox = ScanNotificationOutbox.query.filter_by(scan_id=scan_id).one()
    target = ScanNotificationDelivery.query.filter_by(
        outbox_id=outbox.id, provider_type='healthcheck').one()
    return outbox, target


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_scheduled_success_snapshots_and_delivers_one_healthcheck_target(
        healthcheck_app, monkeypatch):
    _block_broker_dispatch(monkeypatch)
    with healthcheck_app.app_context():
        state_id, config_id, scan_id = _scheduled_run()
        finalize_scan(db.session.get(ScanState, state_id))
        outbox, target = _healthcheck_target(scan_id)
        assert target.provider_id is None
        assert target.rule_id is None
        assert target.status == 'pending'
        assert target.provider_config['mode'] == 'success'
        assert target.provider_config['healthcheck_config_id'] == config_id
        assert target.provider_config['report_data']['scan_id'] == scan_id

        with patch('pixelprobe.services.healthcheck_service.HealthcheckService.ping_success',
                   return_value=True) as send:
            assert deliver_notification_outbox(outbox.id)['status'] == 'DELIVERED'
            assert deliver_notification_outbox(outbox.id)['status'] == 'SKIPPED'
        assert send.call_count == 1
        assert db.session.get(ScanNotificationDelivery, target.id).status == 'delivered'
        assert db.session.get(HealthcheckConfig, config_id).last_ping_status == 'success'


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_scheduled_failure_snapshots_and_delivers_failure_target(healthcheck_app, monkeypatch):
    _block_broker_dispatch(monkeypatch)
    with healthcheck_app.app_context():
        state_id, config_id, scan_id = _scheduled_run(success=False)
        finalize_scan(db.session.get(ScanState, state_id))
        outbox, target = _healthcheck_target(scan_id)
        assert target.provider_config['mode'] == 'failure'
        with patch('pixelprobe.services.healthcheck_service.HealthcheckService.ping_fail',
                   return_value=True) as send:
            assert deliver_notification_outbox(outbox.id)['status'] == 'DELIVERED'
        assert send.call_count == 1
        assert db.session.get(HealthcheckConfig, config_id).last_ping_status == 'failure'


@pytest.mark.parametrize(
    ('config_active', 'send_success', 'skip_reason'),
    [(False, True, 'healthcheck_inactive'), (True, False, 'healthcheck_success_disabled')],
)
@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_disabled_scheduled_healthcheck_is_a_durable_skip(
        healthcheck_app, monkeypatch, config_active, send_success, skip_reason):
    _block_broker_dispatch(monkeypatch)
    with healthcheck_app.app_context():
        state_id, _config_id, scan_id = _scheduled_run(
            config_active=config_active, send_success=send_success)
        finalize_scan(db.session.get(ScanState, state_id))
        outbox, target = _healthcheck_target(scan_id)
        assert target.status == 'skipped'
        assert target.skip_reason == skip_reason
        with patch('pixelprobe.services.healthcheck_service.HealthcheckService.ping_success') as send:
            assert deliver_notification_outbox(outbox.id)['status'] == 'DELIVERED'
        send.assert_not_called()


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_broker_failure_leaves_scheduled_healthcheck_target_durable(healthcheck_app, monkeypatch):
    _block_broker_dispatch(monkeypatch)
    with healthcheck_app.app_context():
        state_id, _config_id, scan_id = _scheduled_run()
        finalize_scan(db.session.get(ScanState, state_id))
        outbox, target = _healthcheck_target(scan_id)
        assert outbox.status == 'pending'
        assert target.status == 'pending'
        assert target.attempts == 0


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_failed_healthcheck_target_retries_without_repeating_delivered_targets(
        healthcheck_app, monkeypatch):
    _block_broker_dispatch(monkeypatch)
    with healthcheck_app.app_context():
        state_id, _config_id, scan_id = _scheduled_run()
        finalize_scan(db.session.get(ScanState, state_id))
        outbox, target = _healthcheck_target(scan_id)
        with patch('pixelprobe.services.healthcheck_service.HealthcheckService.ping_success',
                   side_effect=[False, True]) as send:
            assert deliver_notification_outbox(outbox.id)['status'] == 'RETRY'
            assert db.session.get(ScanNotificationDelivery, target.id).status == 'failed'
            assert deliver_notification_outbox(outbox.id)['status'] == 'DELIVERED'
        assert send.call_count == 2
        assert db.session.get(ScanNotificationDelivery, target.id).attempts == 2


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_old_healthcheck_target_does_not_update_a_reconfigured_destination(
        healthcheck_app, monkeypatch):
    _block_broker_dispatch(monkeypatch)
    with healthcheck_app.app_context():
        state_id, config_id, scan_id = _scheduled_run()
        finalize_scan(db.session.get(ScanState, state_id))
        outbox, target = _healthcheck_target(scan_id)
        config = db.session.get(HealthcheckConfig, config_id)
        config.healthcheck_url = 'https://healthcheck.example.test/new-token'
        db.session.commit()
        with patch('pixelprobe.services.healthcheck_service.HealthcheckService.ping_success',
                   return_value=True):
            assert deliver_notification_outbox(outbox.id)['status'] == 'DELIVERED'
        assert db.session.get(ScanNotificationDelivery, target.id).status == 'delivered'
        assert db.session.get(HealthcheckConfig, config_id).last_ping_status is None

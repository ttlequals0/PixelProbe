"""Celery-free persistence for at-least-once scan notification delivery."""

import re
import uuid
from datetime import datetime, timedelta, timezone

from pixelprobe.models import (db, HealthcheckConfig, NotificationProvider,
                               ScanNotificationDelivery, ScanNotificationOutbox)

NOTIFICATION_LEASE_MINUTES = 5
NOTIFICATION_MAX_ATTEMPTS = 5


def _safe_delivery_error(error):
    """Keep provider failures useful without persisting capability URLs."""
    message = str(error or 'notification provider rejected delivery')
    message = re.sub(r'https?://[^\s\'"<>)]*', '[redacted URL]', message)
    message = re.sub(r'(?i)(token|password|authorization|api[_-]?key)\s*[:=]\s*\S+',
                     r'\1=[redacted]', message)
    return message[:1000]


def _set_outbox_terminal_state(row):
    targets = ScanNotificationDelivery.query.filter_by(outbox_id=row.id).all()
    eligible = [target for target in targets if target.status != 'skipped']
    retryable = any(target.status in {'pending', 'processing'} or
                    (target.status == 'failed' and target.attempts < NOTIFICATION_MAX_ATTEMPTS)
                    for target in eligible)
    terminal_failures = [target for target in eligible
                         if target.status == 'failed' and target.attempts >= NOTIFICATION_MAX_ATTEMPTS]
    if retryable:
        row.status = 'pending'
        row.lease_expires_at = None
        return 'RETRY'
    row.lease_expires_at = None
    if terminal_failures:
        row.status = 'failed'
        row.terminal_reason = 'target_failures_exhausted'
        row.error_message = '; '.join(
            f'target {target.id}: {target.error_message or "delivery failed"}'
            for target in terminal_failures)[:1000]
        return 'FAILED'
    row.status = 'delivered'
    row.delivered_at = datetime.now(timezone.utc)
    row.error_message = None
    row.terminal_reason = 'no_eligible_targets' if not eligible else 'delivered'
    return 'DELIVERED'


def claim_notification_targets(outbox_id):
    """Lease retryable targets and terminally record abandoned exhausted leases."""
    now = datetime.now(timezone.utc)
    row = ScanNotificationOutbox.query.filter_by(id=outbox_id).with_for_update().first()
    if not row or row.status in {'delivered', 'failed'}:
        db.session.commit()
        return 'SKIPPED', []
    if row.status == 'processing' and row.lease_expires_at and row.lease_expires_at > now:
        db.session.commit()
        return 'IN_FLIGHT', []

    row.status = 'processing'
    row.lease_expires_at = now + timedelta(minutes=NOTIFICATION_LEASE_MINUTES)
    row.attempts += 1
    if not row.targets_initialized:
        from pixelprobe.services.notification_service import snapshot_scan_notification_outbox
        snapshot_scan_notification_outbox(row)
        db.session.flush()

    claimed = []
    targets = ScanNotificationDelivery.query.filter_by(outbox_id=row.id).with_for_update().all()
    for target in targets:
        active_lease = (target.status == 'processing' and target.lease_expires_at
                        and target.lease_expires_at > now)
        if (target.status == 'processing' and not active_lease
                and target.attempts >= NOTIFICATION_MAX_ATTEMPTS):
            target.status = 'failed'
            target.lease_expires_at = None
            target.lease_token = None
            target.outcome = 'lease_expired'
            target.error_message = 'delivery acknowledgement lease expired'
            continue
        retryable = target.status == 'pending' or (
            target.status == 'failed' and target.attempts < NOTIFICATION_MAX_ATTEMPTS) or (
            target.status == 'processing' and not active_lease
            and target.attempts < NOTIFICATION_MAX_ATTEMPTS)
        if not retryable:
            continue
        token = str(uuid.uuid4())
        target.status = 'processing'
        target.attempts += 1
        target.lease_expires_at = now + timedelta(minutes=NOTIFICATION_LEASE_MINUTES)
        target.lease_token = token
        target.outcome = None
        claimed.append((target.id, token))

    if not claimed:
        active_leases = [target.lease_expires_at for target in targets
                         if target.status == 'processing' and target.lease_expires_at
                         and target.lease_expires_at > now]
        if active_leases:
            row.status = 'processing'
            row.lease_expires_at = max(active_leases)
            db.session.commit()
            return 'IN_FLIGHT', []
        state = _set_outbox_terminal_state(row)
        db.session.commit()
        return state, []
    db.session.commit()
    return 'PROCESSING', claimed


def renew_notification_target(target_id, lease_token):
    """Renew one target immediately before its external side effect."""
    now = datetime.now(timezone.utc)
    target = ScanNotificationDelivery.query.filter_by(
        id=target_id, status='processing', lease_token=lease_token).with_for_update().first()
    if not target or not target.lease_expires_at or target.lease_expires_at <= now:
        db.session.commit()
        return None
    outbox = db.session.get(ScanNotificationOutbox, target.outbox_id)
    if not outbox or outbox.status in {'delivered', 'failed'}:
        db.session.commit()
        return None
    expiry = now + timedelta(minutes=NOTIFICATION_LEASE_MINUTES)
    target.lease_expires_at = expiry
    outbox.lease_expires_at = expiry
    payload = dict(outbox.payload or {})
    delivery = (target.provider_type, dict(target.provider_config or {}), target.priority, payload)
    db.session.commit()
    return delivery


def record_notification_target(target_id, lease_token, success, error=None):
    """Ack one target only if this worker still owns its lease."""
    target = ScanNotificationDelivery.query.filter_by(
        id=target_id, status='processing', lease_token=lease_token).with_for_update().first()
    if not target:
        db.session.commit()
        return False
    target.lease_expires_at = None
    target.lease_token = None
    if success:
        target.status = 'delivered'
        target.outcome = 'delivered'
        target.error_message = None
        target.delivered_at = datetime.now(timezone.utc)
    else:
        target.status = 'failed'
        target.outcome = ('failed' if target.attempts >= NOTIFICATION_MAX_ATTEMPTS
                          else 'retryable_failure')
        target.error_message = _safe_delivery_error(error)
    provider = (db.session.get(NotificationProvider, target.provider_id)
                if target.provider_id else None)
    if provider:
        provider.last_notification_status = 'success' if success else 'failure'
        provider.last_notification_time = datetime.now(timezone.utc)
    elif target.provider_type == 'healthcheck':
        config_id = (target.provider_config or {}).get('healthcheck_config_id')
        healthcheck = db.session.get(HealthcheckConfig, config_id) if config_id else None
        snapshot_url = (target.provider_config or {}).get('healthcheck_url')
        if healthcheck and healthcheck.healthcheck_url == snapshot_url:
            mode = (target.provider_config or {}).get('mode')
            healthcheck.last_ping_status = (
                'success' if success and mode == 'success' else
                'failure' if success else
                'error' if mode == 'failure' else 'failure'
            )
            healthcheck.last_ping_time = datetime.now(timezone.utc)
    db.session.commit()
    return True


def finish_notification_outbox(outbox_id):
    row = ScanNotificationOutbox.query.filter_by(id=outbox_id).with_for_update().first()
    if not row:
        db.session.commit()
        return 'SKIPPED'
    state = _set_outbox_terminal_state(row)
    db.session.commit()
    return state


def deliver_notification_outbox(outbox_id):
    """Run one persisted outbox delivery attempt without depending on Celery."""
    state, claimed = claim_notification_targets(outbox_id)
    if state != 'PROCESSING':
        return {'status': state, 'outbox_id': outbox_id}
    from pixelprobe.services.notification_service import (deliver_healthcheck_target,
                                                           deliver_notification_target)
    for target_id, token in claimed:
        delivery = renew_notification_target(target_id, token)
        if not delivery:
            continue
        provider_type, provider_config, priority, payload = delivery
        if provider_type == 'healthcheck':
            success, error = deliver_healthcheck_target(provider_config)
        else:
            success, error = deliver_notification_target(
                provider_type, provider_config, payload.get('title', ''),
                payload.get('message', ''), priority, payload.get('additional_data') or {})
        record_notification_target(target_id, token, success, error)
    state = finish_notification_outbox(outbox_id)
    return {'status': state, 'outbox_id': outbox_id, 'targets_claimed': len(claimed)}

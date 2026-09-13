from pixelprobe.api.notification_routes import _validate_conditions
from pixelprobe.models import NotificationProvider, SecurityAuditEvent, User
from pixelprobe.models import db as database


def test_scan_conditions_accept_only_published_fields():
    assert _validate_conditions('scan_completed', {'corrupted_count': '>0'})[1] is None
    assert _validate_conditions('scan_completed', {'unknown_count': '>0'})[1]


def test_bitrot_conditions_reject_scan_only_fields():
    assert _validate_conditions('bitrot_suspected', {'files_scanned': '>0'})[1]
    assert _validate_conditions('bitrot_suspected', {'count': {'operator': 'gte', 'value': 2}})[1] is None


def test_numeric_condition_strings_require_a_real_comparison():
    assert _validate_conditions('scan_completed', {'corrupted_count': 'any'})[1]
    assert _validate_conditions('scan_completed', {'status': 'completed'})[1] is None


def test_numeric_condition_objects_reject_booleans():
    assert _validate_conditions(
        'scan_completed',
        {'corrupted_count': {'operator': 'eq', 'value': True}},
    )[1]


def test_notification_rule_mutations_audit_the_authenticated_actor(
        app, db, authenticated_client):
    with app.app_context():
        provider = NotificationProvider(
            name='audit-provider',
            provider_type='pushover',
            configuration={'api_token': 'test-token', 'user_key': 'test-user'},
        )
        database.session.add(provider)
        database.session.commit()
        provider_id = provider.id
        actor_id = User.query.filter_by(username='testadmin').one().id

    rejected = authenticated_client.post('/api/notifications/rules', json={
        'provider_id': provider_id,
        'event_type': 'scan_completed',
        'conditions': {'corrupted_count': {'operator': 'eq', 'value': True}},
    })
    assert rejected.status_code == 400
    with app.app_context():
        assert SecurityAuditEvent.query.filter_by(
            action='notification_rule_created').count() == 0

    created = authenticated_client.post('/api/notifications/rules', json={
        'provider_id': provider_id,
        'event_type': 'scan_completed',
        'conditions': {'corrupted_count': {'operator': 'gte', 'value': 1}},
    })
    assert created.status_code == 201
    rule_id = created.get_json()['id']

    updated = authenticated_client.put(f'/api/notifications/rules/{rule_id}', json={
        'priority': 'high',
    })
    assert updated.status_code == 200
    deleted = authenticated_client.delete(f'/api/notifications/rules/{rule_id}')
    assert deleted.status_code == 204

    with app.app_context():
        events = SecurityAuditEvent.query.filter(
            SecurityAuditEvent.action.in_([
                'notification_rule_created',
                'notification_rule_updated',
                'notification_rule_deleted',
            ])
        ).order_by(SecurityAuditEvent.id).all()
        assert [event.action for event in events] == [
            'notification_rule_created',
            'notification_rule_updated',
            'notification_rule_deleted',
        ]
        assert all(event.actor_id == actor_id for event in events)
        assert all(event.target == f'notification_rule:{rule_id}' for event in events)
        assert all(event.outcome == 'success' for event in events)

"""Integration tests for per-schedule integrity time budgets (time_budget_minutes)."""

from pixelprobe.models import ScanSchedule


class TestScheduleBudgetValidation:

    def test_create_file_changes_schedule_with_budget(self, authenticated_client, app, db):
        with app.app_context():
            response = authenticated_client.post('/api/schedules', json={
                'name': 'Nightly Integrity',
                'cron_expression': '0 2 * * *',
                'scan_type': 'file_changes',
                'time_budget_minutes': 120
            })
            assert response.status_code == 201
            assert response.get_json()['time_budget_minutes'] == 120

            schedule = ScanSchedule.query.filter_by(name='Nightly Integrity').first()
            assert schedule.time_budget_minutes == 120

    def test_budget_rejected_on_non_file_changes_schedule(self, authenticated_client, app, db):
        with app.app_context():
            response = authenticated_client.post('/api/schedules', json={
                'name': 'Budgeted Normal Scan',
                'cron_expression': '0 2 * * *',
                'scan_type': 'normal',
                'time_budget_minutes': 120
            })
            assert response.status_code == 400
            assert 'file_changes' in response.get_json()['error']

    def test_budget_must_be_positive_integer(self, authenticated_client, app, db):
        with app.app_context():
            for bad in (0, -5, 'sixty', 2.5, True):
                response = authenticated_client.post('/api/schedules', json={
                    'name': f'Bad Budget {bad}',
                    'cron_expression': '0 2 * * *',
                    'scan_type': 'file_changes',
                    'time_budget_minutes': bad
                })
                assert response.status_code == 400, f'accepted invalid budget {bad!r}'

    def test_null_budget_means_unlimited(self, authenticated_client, app, db):
        with app.app_context():
            response = authenticated_client.post('/api/schedules', json={
                'name': 'Unlimited Integrity',
                'cron_expression': '0 2 * * *',
                'scan_type': 'file_changes',
                'time_budget_minutes': None
            })
            assert response.status_code == 201
            assert response.get_json()['time_budget_minutes'] is None

    def test_update_sets_and_clears_budget(self, authenticated_client, app, db):
        with app.app_context():
            schedule = ScanSchedule(
                name='Editable Integrity',
                cron_expression='0 3 * * *',
                scan_type='file_changes',
                is_active=True
            )
            db.session.add(schedule)
            db.session.commit()
            schedule_id = schedule.id

            response = authenticated_client.put(f'/api/schedules/{schedule_id}',
                                                json={'time_budget_minutes': 45})
            assert response.status_code == 200
            assert response.get_json()['time_budget_minutes'] == 45

            response = authenticated_client.put(f'/api/schedules/{schedule_id}',
                                                json={'time_budget_minutes': None})
            assert response.status_code == 200
            assert response.get_json()['time_budget_minutes'] is None

    def test_changing_type_away_from_file_changes_clears_budget(self, authenticated_client, app, db):
        with app.app_context():
            schedule = ScanSchedule(
                name='Type Change',
                cron_expression='0 3 * * *',
                scan_type='file_changes',
                time_budget_minutes=90,
                is_active=True
            )
            db.session.add(schedule)
            db.session.commit()
            schedule_id = schedule.id

            response = authenticated_client.put(f'/api/schedules/{schedule_id}',
                                                json={'scan_type': 'normal'})
            assert response.status_code == 200
            assert response.get_json()['time_budget_minutes'] is None

    def test_unknown_schedule_type_is_rejected_without_a_budget(self, authenticated_client, app, db):
        with app.app_context():
            response = authenticated_client.post('/api/schedules', json={
                'name': 'Unknown Type',
                'cron_expression': '0 2 * * *',
                'scan_type': 'not-a-scan-type',
            })
            assert response.status_code == 400
            assert 'scan_type' in response.get_json()['error']
            assert ScanSchedule.query.filter_by(name='Unknown Type').first() is None

    def test_update_rejects_unrepresentable_interval_without_mutation(self, authenticated_client, app, db):
        with app.app_context():
            schedule = ScanSchedule(
                name='Stable Schedule',
                cron_expression='0 3 * * *',
                scan_type='normal',
                is_active=True,
            )
            db.session.add(schedule)
            db.session.commit()
            schedule_id = schedule.id

            response = authenticated_client.put(f'/api/schedules/{schedule_id}', json={
                'cron_expression': 'interval:days:999999999999999999999999999999999999999999999999999999999999',
            })
            assert response.status_code == 400
            assert 'cron_expression' in response.get_json()['error']
            db.session.expire_all()
            assert db.session.get(ScanSchedule, schedule_id).cron_expression == '0 3 * * *'


class TestManualRunBudgetValidation:

    def test_invalid_manual_budget_rejected(self, authenticated_client, app, db):
        with app.app_context():
            for bad in (0, -10, 'fast', True):
                response = authenticated_client.post('/api/file-changes',
                                                     json={'time_budget_minutes': bad})
                assert response.status_code == 400, f'accepted invalid budget {bad!r}'
                assert 'time_budget_minutes' in response.get_json()['error']

"""Tests for the scanner settings registry, resolution, and API."""

import pytest
from unittest.mock import patch

from pixelprobe.constants import SCANNER_SETTINGS, SCANNER_SETTINGS_BY_KEY, SETTING_GROUPS
from pixelprobe.models import db, AppConfig
from pixelprobe.services.settings_service import (
    coerce_setting, resolve_settings, describe_settings, invalidate_cache,
    SettingValueError)


class TestRegistry:
    """The registry is the single definition every other surface derives from"""

    def test_keys_are_unique(self):
        assert len(SCANNER_SETTINGS_BY_KEY) == len(SCANNER_SETTINGS)

    def test_every_setting_belongs_to_a_declared_group(self):
        groups = {g['key'] for g in SETTING_GROUPS}
        for spec in SCANNER_SETTINGS:
            assert spec['group'] in groups

    def test_key_prefix_matches_group(self):
        for spec in SCANNER_SETTINGS:
            assert spec['key'].split('.')[0] == spec['group']

    def test_every_setting_is_documented_for_the_ui(self):
        for spec in SCANNER_SETTINGS:
            assert spec['label'] and spec['help']
            assert spec['type'] in ('bool', 'int', 'float')

    def test_defaults_pass_their_own_validation(self):
        for spec in SCANNER_SETTINGS:
            assert coerce_setting(spec, spec['default']) == spec['default']


class TestCoercion:
    """Values arrive as strings from both the database and HTTP bodies"""

    def test_bool_accepts_common_spellings(self):
        spec = SCANNER_SETTINGS_BY_KEY['detection.freeze_detection_enabled']
        for raw in ('true', 'True', '1', 'yes', 'on', True):
            assert coerce_setting(spec, raw) is True
        for raw in ('false', '0', 'no', 'off', False):
            assert coerce_setting(spec, raw) is False

    def test_bool_rejects_nonsense(self):
        spec = SCANNER_SETTINGS_BY_KEY['detection.freeze_detection_enabled']
        with pytest.raises(SettingValueError, match='true or false'):
            coerce_setting(spec, 'sometimes')

    def test_range_is_enforced_at_both_ends(self):
        spec = SCANNER_SETTINGS_BY_KEY['detection.data_hole_min_pct']
        with pytest.raises(SettingValueError, match='0 or more'):
            coerce_setting(spec, '-1')
        with pytest.raises(SettingValueError, match='100 or less'):
            coerce_setting(spec, '101')

    def test_non_numeric_is_rejected(self):
        spec = SCANNER_SETTINGS_BY_KEY['detection.freeze_min_duration_secs']
        with pytest.raises(SettingValueError, match='must be a number'):
            coerce_setting(spec, 'soon')

    def test_error_messages_name_the_setting(self):
        spec = SCANNER_SETTINGS_BY_KEY['detection.freeze_min_duration_secs']
        with pytest.raises(SettingValueError, match='Shortest freeze to report'):
            coerce_setting(spec, '0.1')


class TestResolution:
    """Resolution must never be the reason a scan fails"""

    def setup_method(self):
        invalidate_cache()

    def teardown_method(self):
        invalidate_cache()

    def test_defaults_when_nothing_is_stored(self, app):
        with app.app_context():
            db.create_all()
            AppConfig.query.delete()
            db.session.commit()
            values = resolve_settings(use_cache=False)
        assert values['detection.freeze_min_duration_secs'] == 7.0
        assert values['detection.freeze_detection_enabled'] is True

    def test_stored_value_overrides_the_default(self, app):
        with app.app_context():
            db.create_all()
            AppConfig.query.delete()
            db.session.add(AppConfig(key='detection.freeze_min_duration_secs', value='12.5'))
            db.session.commit()
            values = resolve_settings(use_cache=False)
        assert values['detection.freeze_min_duration_secs'] == 12.5

    def test_out_of_range_stored_value_falls_back_to_default(self, app):
        """A hand-edited row or a tightened bound must not reach the scanner"""
        with app.app_context():
            db.create_all()
            AppConfig.query.delete()
            db.session.add(AppConfig(key='detection.data_hole_min_pct', value='900'))
            db.session.commit()
            values = resolve_settings(use_cache=False)
        assert values['detection.data_hole_min_pct'] == 1.0

    def test_unreadable_database_yields_defaults(self, app):
        """A settings read must never be the reason a scan fails"""
        with app.app_context():
            db.create_all()
            with patch('pixelprobe.models.AppConfig.query') as broken:
                broken.filter.side_effect = Exception('database down')
                values = resolve_settings(use_cache=False)
        assert values['detection.freeze_min_duration_secs'] == 7.0
        assert len(values) == len(SCANNER_SETTINGS)

    def test_describe_reports_whether_a_value_is_default(self, app):
        with app.app_context():
            db.create_all()
            AppConfig.query.delete()
            db.session.add(AppConfig(key='detection.freeze_min_duration_secs', value='30'))
            db.session.commit()
            described = {s['key']: s for s in describe_settings()}
        assert described['detection.freeze_min_duration_secs']['is_default'] is False
        assert described['detection.static_card_edge_secs']['is_default'] is True


class TestSettingsApi:
    """The API is the contract the UI and any script depend on"""

    def setup_method(self):
        invalidate_cache()

    def test_get_returns_every_setting_grouped(self, authenticated_client, app):
        with app.app_context():
            db.create_all()
            AppConfig.query.delete()
            db.session.commit()
        resp = authenticated_client.get('/api/settings')
        assert resp.status_code == 200
        groups = resp.get_json()['groups']
        assert {g['key'] for g in groups} == {g['key'] for g in SETTING_GROUPS}
        total = sum(len(g['settings']) for g in groups)
        assert total == len(SCANNER_SETTINGS)

    def test_put_saves_and_reports_the_new_value(self, authenticated_client, app):
        resp = authenticated_client.put('/api/settings',
                          json={'detection.freeze_min_duration_secs': 9})
        assert resp.status_code == 200
        described = {s['key']: s for s in resp.get_json()['settings']}
        assert described['detection.freeze_min_duration_secs']['value'] == 9.0

    def test_put_rejects_an_unknown_key(self, authenticated_client):
        resp = authenticated_client.put('/api/settings', json={'detection.nope': 1})
        assert resp.status_code == 400
        assert 'Unknown setting' in resp.get_json()['error']

    def test_put_rejects_an_out_of_range_value(self, authenticated_client):
        resp = authenticated_client.put('/api/settings',
                          json={'detection.data_hole_min_pct': 500})
        assert resp.status_code == 400
        assert '100 or less' in resp.get_json()['error']

    def test_a_bad_value_leaves_the_batch_unwritten(self, authenticated_client, app):
        """Validation happens before any write, so a rejected batch changes nothing"""
        with app.app_context():
            db.create_all()
            AppConfig.query.delete()
            db.session.commit()
        resp = authenticated_client.put('/api/settings', json={
            'detection.freeze_min_duration_secs': 11,
            'detection.data_hole_min_pct': 500,
        })
        assert resp.status_code == 400
        with app.app_context():
            assert AppConfig.query.filter_by(
                key='detection.freeze_min_duration_secs').first() is None

    def test_put_rejects_a_non_object_body(self, authenticated_client):
        resp = authenticated_client.put('/api/settings', json=[1, 2])
        assert resp.status_code == 400

    def test_delete_restores_the_default(self, authenticated_client):
        authenticated_client.put('/api/settings',
                   json={'detection.freeze_min_duration_secs': 25})
        resp = authenticated_client.delete('/api/settings/detection.freeze_min_duration_secs')
        assert resp.status_code == 200
        described = {s['key']: s for s in resp.get_json()['settings']}
        assert described['detection.freeze_min_duration_secs']['value'] == 7.0
        assert described['detection.freeze_min_duration_secs']['is_default'] is True

    def test_delete_rejects_an_unknown_key(self, authenticated_client):
        resp = authenticated_client.delete('/api/settings/detection.nope')
        assert resp.status_code == 404

    def test_settings_require_authentication(self, app):
        """An unauthenticated caller cannot read or change scanner behaviour.

        Uses its own client: the shared fixture is session-scoped and carries
        the logged-in cookie from whichever test authenticated first.
        """
        anonymous = app.test_client()
        assert anonymous.get('/api/settings').status_code in (401, 403)
        assert anonymous.put('/api/settings',
                             json={'detection.freeze_min_duration_secs': 9}
                             ).status_code in (401, 403)

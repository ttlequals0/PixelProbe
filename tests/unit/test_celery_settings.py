"""Guard against old-style Celery keys leaking into app.config.

celery.conf.update(app.config) raises ImproperlyConfigured ("Cannot mix new
and old setting keys") when app.config contains any old-style Celery setting
key. An uppercase CELERY_RESULT_BACKEND attribute on Config did exactly that
and took production down on 2026-06-11 (v2.6.55). CELERY_BROKER_URL is safe
only because the old-style broker key was BROKER_URL.
"""

import os

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from celery import Celery
from celery.app.defaults import _OLD_SETTING_KEYS

from pixelprobe.config import Config


def _uppercase_config():
    return {k: getattr(Config, k) for k in dir(Config) if k.isupper()}


class TestNoOldStyleCeleryKeysInConfig:

    def test_no_uppercase_config_attr_is_old_style_celery_key(self):
        offenders = sorted(set(_uppercase_config()) & _OLD_SETTING_KEYS)
        assert offenders == [], (
            f"Old-style Celery keys in Config break celery.conf.update at "
            f"boot: {offenders}")

    def test_celery_conf_finalizes_with_app_config(self):
        celery = Celery('t', broker='redis://localhost:6379/0',
                        backend='redis://localhost:6379/0')
        celery.conf.update({'task_serializer': 'json'})
        celery.conf.update(_uppercase_config())
        assert celery.conf.result_backend  # forces detect_settings

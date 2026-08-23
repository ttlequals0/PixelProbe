"""Resolution of scanner settings stored in the database.

Settings used to be module-level constants read from the environment at import
time, which meant a change needed a container restart and could not be made
from the UI. They now live in `AppConfig`, and this module turns the registry
in `constants.py` plus those stored rows into resolved values.

The scanner asks for settings once per file, so the read is cached for a short
interval rather than hitting the database each time. A Celery worker therefore
picks up an edit within `SETTINGS_CACHE_TTL_SECS` without a restart.

Kept free of Celery and Flask-request imports so it is unit-testable and usable
from both the web process and the workers.
"""

import logging
import threading
import time

from pixelprobe.constants import SCANNER_SETTINGS, SCANNER_SETTINGS_BY_KEY

logger = logging.getLogger(__name__)

# Long enough that a 20-worker scan is not querying constantly, short enough
# that an edit takes effect while the operator is still watching for it.
SETTINGS_CACHE_TTL_SECS = 60

_cache = {'values': None, 'expires_at': 0.0}
_cache_lock = threading.Lock()


class SettingValueError(ValueError):
    """A supplied setting value is the wrong type or outside its range."""


def coerce_setting(spec, raw):
    """Coerce and range-check one value against its registry entry.

    Accepts the string forms the database and HTTP bodies carry as well as
    real bools/numbers. Raises SettingValueError with a message written for
    the person who typed the value.
    """
    label = spec.get('label', spec['key'])
    kind = spec['type']

    if kind == 'bool':
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ('true', '1', 'yes', 'on'):
            return True
        if text in ('false', '0', 'no', 'off'):
            return False
        raise SettingValueError(f"{label} must be true or false")

    try:
        value = int(raw) if kind == 'int' else float(raw)
    except (TypeError, ValueError):
        expected = 'a whole number' if kind == 'int' else 'a number'
        raise SettingValueError(f"{label} must be {expected}")

    low, high = spec.get('min'), spec.get('max')
    if low is not None and value < low:
        raise SettingValueError(f"{label} must be {_plain(low)} or more")
    if high is not None and value > high:
        raise SettingValueError(f"{label} must be {_plain(high)} or less")
    return value


def _plain(number):
    """Format a bound without a trailing .0 on whole numbers."""
    return str(int(number)) if float(number).is_integer() else str(number)


def _load_stored():
    """Read stored overrides. Returns {} when the table cannot be read."""
    try:
        from pixelprobe.models import AppConfig
        rows = AppConfig.query.filter(
            AppConfig.key.in_(list(SCANNER_SETTINGS_BY_KEY))).all()
        return {row.key: row.value for row in rows}
    except Exception as e:
        # A settings read must never be the reason a scan fails: fall back to
        # the registry defaults and say so once per cache interval.
        logger.warning(f"Could not read scanner settings, using defaults: {e}")
        return {}


def resolve_settings(use_cache=True):
    """Return {key: value} for every registered setting.

    A stored value that no longer passes validation (a hand-edited row, or a
    bound tightened by an upgrade) is ignored in favour of the default rather
    than propagated into the scanner.
    """
    if use_cache:
        with _cache_lock:
            if _cache['values'] is not None and time.time() < _cache['expires_at']:
                return dict(_cache['values'])

    stored = _load_stored()
    values = {}
    for spec in SCANNER_SETTINGS:
        raw = stored.get(spec['key'])
        if raw is None:
            values[spec['key']] = spec['default']
            continue
        try:
            values[spec['key']] = coerce_setting(spec, raw)
        except SettingValueError as e:
            logger.warning(f"Ignoring stored value for {spec['key']}: {e}")
            values[spec['key']] = spec['default']

    if use_cache:
        with _cache_lock:
            _cache['values'] = dict(values)
            _cache['expires_at'] = time.time() + SETTINGS_CACHE_TTL_SECS
    return values


def invalidate_cache():
    """Drop the cached values so the next read reflects a just-saved change."""
    with _cache_lock:
        _cache['values'] = None
        _cache['expires_at'] = 0.0


def describe_settings():
    """Registry plus current values, shaped for the API and the settings UI."""
    values = resolve_settings(use_cache=False)
    described = []
    for spec in SCANNER_SETTINGS:
        described.append({
            'key': spec['key'],
            'group': spec['group'],
            'label': spec['label'],
            'help': spec['help'],
            'type': spec['type'],
            'value': values[spec['key']],
            'default': spec['default'],
            'min': spec.get('min'),
            'max': spec.get('max'),
            'unit': spec.get('unit'),
            'is_default': values[spec['key']] == spec['default'],
        })
    return described

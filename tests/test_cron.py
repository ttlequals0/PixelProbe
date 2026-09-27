from datetime import datetime, timezone

import pytest

from pixelprobe.utils.cron import crontab_day_of_week, crontab_trigger


@pytest.mark.parametrize(('field', 'expected'), [
    ('0', 'sun'),
    ('7', 'sun'),
    ('1-5', 'mon,tue,wed,thu,fri'),
    ('5-7', 'sun,fri,sat'),
    ('0,6', 'sun,sat'),
    ('*/2', 'sun,tue,thu,sat'),
    ('*', '*'),
    ('mon-fri', 'mon-fri'),
])
def test_crontab_day_of_week(field, expected):
    assert crontab_day_of_week(field) == expected


@pytest.mark.parametrize('field', ['8', '5-2', '*/0', '9-1,2'])
def test_crontab_day_of_week_rejects_invalid(field):
    with pytest.raises(ValueError):
        crontab_day_of_week(field)


def test_weekday_zero_fires_on_sunday():
    trigger = crontab_trigger('0 2 * * 0', timezone='UTC')
    # 2026-09-22 is a Tuesday; APScheduler's own numbering would pick Monday 09-28
    fire = trigger.get_next_fire_time(None, datetime(2026, 9, 22, tzinfo=timezone.utc))
    assert fire == datetime(2026, 9, 27, 2, 0, tzinfo=timezone.utc)

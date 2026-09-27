"""Crontab parsing shared by the scheduler and the schedule API."""
from apscheduler.triggers.cron import CronTrigger

_DOW_NAMES = ('sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat')


def crontab_day_of_week(field):
    """Translate crontab day-of-week numbers (0 or 7 = Sunday) to day names.

    APScheduler numbers days from Monday, so '0' would fire on Monday.
    Names already mean the same thing in both and pass through unchanged.
    """
    if field == '*' or any(c.isalpha() for c in field):
        return field
    days = set()
    for part in field.split(','):
        span, _, step = part.partition('/')
        if span == '*':
            low, high = 0, 6
        elif '-' in span:
            low, high = (int(x) for x in span.split('-', 1))
        else:
            low = int(span)
            high = 6 if step else low
        step = int(step) if step else 1
        if not (0 <= low <= high <= 7) or step < 1:
            raise ValueError(f"Invalid day of week: {field}")
        days.update(day % 7 for day in range(low, high + 1, step))
    return ','.join(_DOW_NAMES[day] for day in sorted(days))


def crontab_trigger(cron_expr, **kwargs):
    """Build a CronTrigger from a five-field crontab expression."""
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {cron_expr}")
    return CronTrigger(minute=parts[0], hour=parts[1], day=parts[2], month=parts[3],
                       day_of_week=crontab_day_of_week(parts[4]), **kwargs)

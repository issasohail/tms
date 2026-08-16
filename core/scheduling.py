from datetime import datetime, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo

from django.utils import timezone


PAKISTAN_TIME_ZONE = ZoneInfo("Asia/Karachi")
SCHEDULER_WINDOW_MINUTES = 5


def pakistan_local_now(now=None):
    current = now or timezone.now()
    if timezone.is_naive(current):
        current = current.replace(tzinfo=datetime_timezone.utc)
    return current.astimezone(PAKISTAN_TIME_ZONE)


def scheduler_time_is_due(configured_time, *, now=None, window_minutes=None):
    """Return True during the configured Pakistan-time scheduler window."""
    if configured_time is None:
        return False
    local_now = pakistan_local_now(now)
    scheduled_at = datetime.combine(
        local_now.date(),
        configured_time.replace(tzinfo=None),
        tzinfo=PAKISTAN_TIME_ZONE,
    )
    window = timedelta(
        minutes=window_minutes or SCHEDULER_WINDOW_MINUTES
    )
    return scheduled_at <= local_now < scheduled_at + window


def format_scheduler_time(configured_time):
    return configured_time.strftime("%H:%M") if configured_time else "not configured"

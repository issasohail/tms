from django.conf import settings


def online_threshold_minutes() -> int:
    """Return the shared persisted-reading freshness window used by every UI."""
    return int(settings.SMART_METER_ONLINE_THRESHOLD_MINUTES)

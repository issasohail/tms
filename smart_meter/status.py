from django.conf import settings
from datetime import datetime

from django.utils import timezone

from smart_meter.services.meter_presence import (
    MeterPresence,
    get_meter_presence,
    get_meter_presences,
    presence_ttl_seconds,
)


def online_threshold_minutes() -> int:
    """Return the shared persisted-reading freshness window used by every UI."""
    return int(settings.SMART_METER_ONLINE_THRESHOLD_MINUTES)


def resolve_meter_online_status(meter, live_reading, presence=None) -> dict:
    """Resolve transport reachability separately from measurement freshness."""
    last_measurement_at = (
        live_reading
        if isinstance(live_reading, datetime)
        else getattr(live_reading, "ts", None)
    )
    now = timezone.now()
    measurement_is_fresh = bool(
        last_measurement_at
        and (now - last_measurement_at).total_seconds()
        <= online_threshold_minutes() * 60
    )
    if presence is None:
        presence = get_meter_presence(meter.meter_number)

    if not presence.available:
        # Safe compatibility mode: Redis failure retains the former ts-based rule.
        is_connected = measurement_is_fresh
        last_contact_at = last_measurement_at
    else:
        contact_is_recent = bool(
            presence.last_contact_at
            and (now - presence.last_contact_at).total_seconds()
            <= presence_ttl_seconds()
        )
        is_connected = bool(presence.connected and contact_is_recent)
        last_contact_at = presence.last_contact_at

    if not is_connected:
        connection_state = "offline"
    elif measurement_is_fresh:
        connection_state = "online"
    else:
        connection_state = "stale"

    return {
        "connection_state": connection_state,
        "is_online": is_connected,
        "is_connected": is_connected,
        "measurement_is_fresh": measurement_is_fresh,
        "last_contact_at": last_contact_at,
        "last_measurement_at": last_measurement_at,
        "source_ip": presence.source_ip if presence.available else None,
        "source_port": presence.source_port if presence.available else None,
    }


def resolve_meter_online_statuses(meter_reading_pairs) -> dict[int, dict]:
    """Bulk Redis lookup for list/dashboard views."""
    pairs = list(meter_reading_pairs)
    presences = get_meter_presences(meter.meter_number for meter, _reading in pairs)
    return {
        meter.pk: resolve_meter_online_status(
            meter,
            reading,
            presences.get(meter.meter_number, MeterPresence(available=False)),
        )
        for meter, reading in pairs
    }

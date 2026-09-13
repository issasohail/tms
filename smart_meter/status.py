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
        is_online = measurement_is_fresh
        last_contact_at = last_measurement_at
    else:
        socket_seen_at = presence.socket_seen_at or presence.last_contact_at
        contact_is_recent = bool(
            socket_seen_at
            and (now - socket_seen_at).total_seconds()
            <= presence_ttl_seconds()
        )
        is_connected = bool(presence.connected and contact_is_recent)
        # These meters commonly upload a valid frame and then close the TCP
        # session normally. A recent valid measurement therefore still means
        # the meter is online even when there is no socket open at this instant.
        is_online = bool(measurement_is_fresh or is_connected)
        last_contact_at = presence.last_contact_at

    if measurement_is_fresh:
        connection_state = "online"
    elif is_connected:
        connection_state = "stale"
    else:
        connection_state = "offline"

    return {
        "connection_state": connection_state,
        "is_online": is_online,
        "is_connected": is_connected,
        "measurement_is_fresh": measurement_is_fresh,
        "last_contact_at": last_contact_at,
        "last_measurement_at": last_measurement_at,
        "source_ip": presence.source_ip if presence.available else None,
        "source_port": presence.source_port if presence.available else None,
    }


def _duration_label(seconds) -> str:
    seconds = max(0, int(seconds or 0))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def build_reconnect_gap_diagnostics(events, now=None) -> dict:
    """Summarize meaningful transport gaps from connection events.

    A "replaced" disconnect is not treated as an outage because it is emitted
    when a newer socket takes ownership of the same meter.
    """
    now = now or timezone.now()
    ordered = sorted(events, key=lambda event: event.occurred_at)
    pending_disconnect = None
    completed = []
    latest_disconnect = None

    for event in ordered:
        if event.event_type == "disconnected":
            if (getattr(event, "disconnect_reason", "") or "") == "replaced":
                continue
            latest_disconnect = event
            if pending_disconnect is None:
                pending_disconnect = event
            continue

        if (
            event.event_type in {"connected", "reconnected"}
            and pending_disconnect is not None
            and event.occurred_at >= pending_disconnect.occurred_at
        ):
            seconds = (event.occurred_at - pending_disconnect.occurred_at).total_seconds()
            completed.append({
                "started_at": pending_disconnect.occurred_at,
                "ended_at": event.occurred_at,
                "seconds": max(0.0, seconds),
                "label": _duration_label(seconds),
                "reason": getattr(pending_disconnect, "disconnect_reason", "") or "-",
            })
            pending_disconnect = None

    current_gap = None
    if pending_disconnect is not None:
        seconds = (now - pending_disconnect.occurred_at).total_seconds()
        current_gap = {
            "started_at": pending_disconnect.occurred_at,
            "seconds": max(0.0, seconds),
            "label": _duration_label(seconds),
            "reason": getattr(pending_disconnect, "disconnect_reason", "") or "-",
        }

    return {
        "current_gap": current_gap,
        "last_completed_gap": completed[-1] if completed else None,
        "longest_completed_gap": max(completed, key=lambda item: item["seconds"]) if completed else None,
        "latest_disconnect": latest_disconnect,
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

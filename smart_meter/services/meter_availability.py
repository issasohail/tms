from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from smart_meter.models import LiveReading, MeterReading
from smart_meter.utils.display import attach_active_meter_counts


DEFAULT_CADENCE_MINUTES = 15
DEFAULT_OFFLINE_GAP_MINUTES = 25


def _duration_label(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _percent(value, start_at, total_seconds):
    seconds = (value - start_at).total_seconds()
    return max(0.0, min(100.0, (seconds / total_seconds) * 100.0))


def build_meter_availability_timeline(
    meters,
    start_at,
    end_at,
    *,
    role_labels=None,
    cadence_minutes: int = DEFAULT_CADENCE_MINUTES,
    offline_gap_minutes: int = DEFAULT_OFFLINE_GAP_MINUTES,
):
    """Build a per-meter online/offline timeline from persisted meter readings.

    The meters cannot report a zero-voltage frame after their own supply is lost.
    Consequently, historical "offline" periods are inferred from missing
    MeterReading snapshots.  With a normal 15-minute snapshot cadence, a gap of
    25 minutes or more is treated as an offline/communications interval.

    This is deliberately per meter.  It does not assume that all meters must fail
    together, which supports sites where grid-fed meters, high-load circuits and
    battery-backed circuits lose supply at different times.
    """
    role_labels = role_labels or {}
    unique = {}
    for meter in meters:
        if meter is not None and getattr(meter, "pk", None):
            unique[meter.pk] = meter
    meters = attach_active_meter_counts(list(unique.values()))

    if timezone.is_naive(start_at):
        start_at = timezone.make_aware(start_at, timezone.get_current_timezone())
    if timezone.is_naive(end_at):
        end_at = timezone.make_aware(end_at, timezone.get_current_timezone())
    if end_at <= start_at:
        end_at = start_at + timedelta(minutes=1)

    total_seconds = max(60.0, (end_at - start_at).total_seconds())
    meter_ids = [meter.pk for meter in meters]
    pad = timedelta(minutes=max(offline_gap_minutes, cadence_minutes) * 2)

    history_by_meter = defaultdict(list)
    if meter_ids:
        for meter_id, ts in (
            MeterReading.objects.filter(
                meter_id__in=meter_ids,
                ts__gte=start_at - pad,
                ts__lte=end_at + pad,
            )
            .order_by("meter_id", "ts", "id")
            .values_list("meter_id", "ts")
        ):
            history_by_meter[meter_id].append(ts)

    live_by_meter = {
        live.meter_id: live
        for live in LiveReading.objects.filter(meter_id__in=meter_ids)
    }

    now = timezone.now()
    range_reaches_now = start_at <= now and end_at >= now - timedelta(minutes=2)
    cadence = timedelta(minutes=cadence_minutes)
    offline_threshold = timedelta(minutes=offline_gap_minutes)

    rows = []
    for meter in sorted(
        meters,
        key=lambda item: (
            getattr(getattr(item, "unit", None), "unit_number", "") or "",
            item.meter_number,
        ),
    ):
        timestamps = list(history_by_meter.get(meter.pk, []))
        live = live_by_meter.get(meter.pk)
        live_in_window = (
            live
            if live is not None
            and live.ts
            and start_at - pad <= live.ts <= end_at + pad
            else None
        )

        # LiveReading is updated on every accepted frame and is therefore more
        # precise than the 15-minute historical snapshot for the current edge.
        if live_in_window is not None:
            if not timestamps or live_in_window.ts > timestamps[-1]:
                timestamps.append(live_in_window.ts)

        timestamps = sorted(set(timestamps))
        offline_events = []

        for previous_ts, next_ts in zip(timestamps, timestamps[1:]):
            if next_ts - previous_ts < offline_threshold:
                continue
            offline_from = previous_ts + cadence
            offline_until = next_ts
            clipped_start = max(start_at, offline_from)
            clipped_end = min(end_at, offline_until)
            if clipped_end <= clipped_start:
                continue
            offline_events.append({
                "last_seen_at": previous_ts,
                "offline_from": offline_from,
                "online_at": next_ts,
                "clipped_start": clipped_start,
                "clipped_end": clipped_end,
                "ongoing": False,
                "duration_seconds": (offline_until - offline_from).total_seconds(),
                "duration_label": _duration_label(
                    (offline_until - offline_from).total_seconds()
                ),
            })

        latest_contact = None
        if range_reaches_now and live is not None and live.ts:
            latest_contact = live.ts
        else:
            in_period_timestamps = [ts for ts in timestamps if ts <= end_at]
            if in_period_timestamps:
                latest_contact = in_period_timestamps[-1]

        current_offline = False
        if (
            range_reaches_now
            and latest_contact is not None
            and now - latest_contact >= offline_threshold
        ):
            current_offline = True
            offline_from = latest_contact + (
                timedelta(0)
                if live is not None and live.ts == latest_contact
                else cadence
            )
            clipped_start = max(start_at, offline_from)
            clipped_end = min(end_at, now)
            if clipped_end > clipped_start:
                # Avoid duplicating a historical event that already covers this edge.
                if not offline_events or offline_events[-1]["online_at"] is not None:
                    offline_events.append({
                        "last_seen_at": latest_contact,
                        "offline_from": offline_from,
                        "online_at": None,
                        "clipped_start": clipped_start,
                        "clipped_end": clipped_end,
                        "ongoing": True,
                        "duration_seconds": (now - offline_from).total_seconds(),
                        "duration_label": _duration_label(
                            (now - offline_from).total_seconds()
                        ),
                    })

        segments = []
        for event in offline_events:
            left = _percent(event["clipped_start"], start_at, total_seconds)
            right = _percent(event["clipped_end"], start_at, total_seconds)
            width = max(0.35, right - left)
            if left + width > 100:
                width = max(0.0, 100 - left)
            segments.append({
                "left": f"{left:.4f}",
                "width": f"{width:.4f}",
                "ongoing": event["ongoing"],
                "title": (
                    f"Offline approx {timezone.localtime(event['offline_from']):%d %b %Y %H:%M}"
                    + (
                        " → now"
                        if event["online_at"] is None
                        else f" → {timezone.localtime(event['online_at']):%d %b %Y %H:%M}"
                    )
                    + f" ({event['duration_label']})"
                ),
            })

        visible_timestamps = [
            ts for ts in timestamps if start_at - pad <= ts <= end_at + pad
        ]
        if current_offline:
            state = "offline"
            state_label = "Offline"
        elif not visible_timestamps and live_in_window is None:
            state = "unknown"
            state_label = "No readings"
        else:
            state = "online"
            state_label = "Online"

        reading_value = None
        if range_reaches_now and live is not None:
            reading_value = (
                live.forward_active_energy_kwh
                if live.forward_active_energy_kwh is not None
                else live.total_energy
            )

        roles = role_labels.get(meter.pk) or []
        detail_source = "input" if "Input" in roles else ""
        dashboard_role = "check" if ("Input" in roles or "Audit" in roles) else "billing"
        rows.append({
            "meter": meter,
            "property_name": (
                meter.unit.property.property_name
                if meter.unit_id and meter.unit and meter.unit.property_id
                else ""
            ),
            "display_name": meter.display_location_name,
            "roles": roles,
            "detail_source": detail_source,
            "dashboard_role": dashboard_role,
            "state": state,
            "state_label": state_label,
            "latest_contact": latest_contact,
            "latest_reading": reading_value,
            "events": offline_events,
            "segments": segments,
            "offline_count": len(offline_events),
        })

    axis_labels = []
    for fraction in (0, 0.25, 0.50, 0.75, 1):
        axis_labels.append(start_at + (end_at - start_at) * fraction)

    return {
        "rows": rows,
        "start_at": start_at,
        "end_at": end_at,
        "axis_labels": axis_labels,
        "start_epoch_ms": int(start_at.timestamp() * 1000),
        "end_epoch_ms": int(end_at.timestamp() * 1000),
        "cadence_minutes": cadence_minutes,
        "offline_gap_minutes": offline_gap_minutes,
    }

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from smart_meter.models import Meter, MeterCommunicationAlert, MeterConnectionEvent


def communication_alert_threshold_minutes():
    return int(getattr(settings, "SMART_METER_COMMUNICATION_ALERT_MINUTES", 30))


def _latest_disconnect(meter_number):
    return (
        MeterConnectionEvent.objects.filter(
            meter_number=meter_number,
            event_type=MeterConnectionEvent.EVENT_DISCONNECTED,
        )
        .exclude(disconnect_reason="replaced")
        .order_by("-occurred_at", "-id")
        .first()
    )


def evaluate_meter_communication_alerts(*, now=None, threshold_minutes=None, meter_number=None, dry_run=False):
    """Create/resolve persistent alerts from valid LiveReading freshness."""
    now = now or timezone.now()
    threshold_minutes = int(
        threshold_minutes
        if threshold_minutes is not None
        else communication_alert_threshold_minutes()
    )
    if threshold_minutes < 1:
        raise ValueError("threshold_minutes must be at least 1")

    cutoff = now - timedelta(minutes=threshold_minutes)
    meters = Meter.objects.select_related("live").filter(live__isnull=False)
    if meter_number:
        meters = meters.filter(meter_number=meter_number)

    result = {
        "checked": 0,
        "created": 0,
        "resolved": 0,
        "still_open": 0,
        "dry_run": bool(dry_run),
        "actions": [],
    }

    for meter in meters.iterator():
        result["checked"] += 1
        live = getattr(meter, "live", None)
        last_reading_at = getattr(live, "ts", None)
        if last_reading_at is None:
            continue

        with transaction.atomic():
            open_alert = (
                MeterCommunicationAlert.objects.select_for_update()
                .filter(meter=meter, status=MeterCommunicationAlert.STATUS_OPEN)
                .order_by("-opened_at", "-id")
                .first()
            )

            if last_reading_at <= cutoff:
                if open_alert is not None:
                    result["still_open"] += 1
                    continue

                disconnect = _latest_disconnect(meter.meter_number)
                values = {
                    "meter": meter,
                    "status": MeterCommunicationAlert.STATUS_OPEN,
                    "threshold_minutes": threshold_minutes,
                    "last_reading_at": last_reading_at,
                    "last_disconnect_at": getattr(disconnect, "occurred_at", None),
                    "disconnect_reason": getattr(disconnect, "disconnect_reason", "") or "",
                    "last_source_ip": getattr(disconnect, "source_ip", None),
                    "last_source_port": getattr(disconnect, "source_port", None),
                }
                result["actions"].append(("created", meter.meter_number))
                result["created"] += 1
                if not dry_run:
                    MeterCommunicationAlert.objects.create(**values)
                continue

            if open_alert is None:
                continue

            offline_seconds = max(
                0,
                int((last_reading_at - open_alert.last_reading_at).total_seconds()),
            )
            result["actions"].append(("resolved", meter.meter_number))
            result["resolved"] += 1
            if not dry_run:
                open_alert.status = MeterCommunicationAlert.STATUS_RESOLVED
                open_alert.resolved_at = now
                open_alert.restored_reading_at = last_reading_at
                open_alert.offline_duration_seconds = offline_seconds
                open_alert.save(update_fields=[
                    "status",
                    "resolved_at",
                    "restored_reading_at",
                    "offline_duration_seconds",
                ])

    return result

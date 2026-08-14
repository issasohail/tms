from django.db.models import Count

from smart_meter.models import MeterInstallation


def attach_active_meter_counts(items, meter_getter=None):
    """Attach active installation counts to meters with one aggregate query."""
    materialized = list(items)
    getter = meter_getter or (lambda item: item)
    meters = []
    unit_ids = set()

    for item in materialized:
        meter = getter(item)
        if meter is None:
            continue
        meters.append(meter)
        if meter.unit_id:
            unit_ids.add(meter.unit_id)

    counts = {
        row["unit_id"]: row["total"]
        for row in (
            MeterInstallation.objects.filter(
                unit_id__in=unit_ids,
                is_active=True,
                end_date__isnull=True,
            )
            .values("unit_id")
            .annotate(total=Count("id"))
        )
    }
    for meter in meters:
        meter._active_unit_meter_count = counts.get(meter.unit_id, 0)
    return materialized

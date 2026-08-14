from collections import defaultdict

from smart_meter.models import Meter, MeterInstallation


def _active_meter_ids_by_unit(unit_ids):
    """Return current meter IDs by unit, including legacy cached assignments."""
    unit_ids = {unit_id for unit_id in unit_ids if unit_id}
    meter_ids_by_unit = defaultdict(set)
    if not unit_ids:
        return meter_ids_by_unit

    for unit_id, meter_id in MeterInstallation.objects.filter(
        unit_id__in=unit_ids,
        is_active=True,
        end_date__isnull=True,
    ).values_list("unit_id", "meter_id"):
        meter_ids_by_unit[unit_id].add(meter_id)

    # Some older/current assignments pre-date MeterInstallation history. Include
    # active meters whose cached unit still points at the unit.
    for unit_id, meter_id in Meter.objects.filter(
        unit_id__in=unit_ids,
        is_active=True,
    ).values_list("unit_id", "id"):
        meter_ids_by_unit[unit_id].add(meter_id)

    return meter_ids_by_unit


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

    meter_ids_by_unit = _active_meter_ids_by_unit(unit_ids)
    for meter in meters:
        meter._active_unit_meter_count = len(meter_ids_by_unit.get(meter.unit_id, ()))
    return materialized


def display_labels_for_units(units):
    """Map unit IDs to unit names, or joined meter names for multi-meter units."""
    materialized = list(units)
    unit_ids = [unit.id for unit in materialized]
    meter_ids_by_unit = _active_meter_ids_by_unit(unit_ids)
    all_meter_ids = {
        meter_id
        for meter_ids in meter_ids_by_unit.values()
        for meter_id in meter_ids
    }
    meters = {
        meter.id: meter
        for meter in Meter.objects.filter(id__in=all_meter_ids).only(
            "id", "meter_number", "name"
        )
    }

    labels = {}
    for unit in materialized:
        meter_ids = meter_ids_by_unit.get(unit.id, set())
        if len(meter_ids) > 1:
            unit_meters = sorted(
                (meters[meter_id] for meter_id in meter_ids if meter_id in meters),
                key=lambda meter: meter.meter_number,
            )
            labels[unit.id] = " / ".join(
                (meter.name or "").strip() or meter.meter_number
                for meter in unit_meters
            )
        else:
            labels[unit.id] = unit.unit_number
    return labels

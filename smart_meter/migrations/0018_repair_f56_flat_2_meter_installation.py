from datetime import date
from decimal import Decimal

from django.db import migrations


REPAIR_REASON = "repair_f56_flat_2_meter_installation_0018"
METER_NUMBER = "260305510017"
MOVE_DATE = date(2026, 6, 7)
OLD_END_DATE = date(2026, 6, 6)


def _reading_value(MeterReading, meter, reading_date, *, after):
    readings = MeterReading.objects.filter(meter=meter)
    if after:
        reading = (
            readings.filter(ts__date__gte=reading_date)
            .order_by("ts", "id")
            .first()
        )
    else:
        reading = (
            readings.filter(ts__date__lte=reading_date)
            .order_by("-ts", "-id")
            .first()
        )
    return getattr(reading, "total_energy", None)


def repair_flat_2_meter_installation(apps, schema_editor):
    Lease = apps.get_model("leases", "Lease")
    Unit = apps.get_model("properties", "Unit")
    Meter = apps.get_model("smart_meter", "Meter")
    MeterAssignmentHistory = apps.get_model("smart_meter", "MeterAssignmentHistory")
    MeterInstallation = apps.get_model("smart_meter", "MeterInstallation")
    MeterReading = apps.get_model("smart_meter", "MeterReading")

    meter = Meter.objects.filter(meter_number=METER_NUMBER).first()
    target_unit = Unit.objects.filter(
        property__property_name="F56",
        unit_number="F56-FLAT# 02",
    ).first()
    if not meter or not target_unit:
        return

    target_lease = (
        Lease.objects.filter(
            unit=target_unit,
            status="active",
            start_date=date(2026, 8, 1),
        )
        .order_by("id")
        .first()
    )
    if not target_lease:
        return

    old_end_reading = _reading_value(
        MeterReading, meter, OLD_END_DATE, after=False
    )
    active_installations = MeterInstallation.objects.filter(
        meter=meter,
        is_active=True,
        end_date__isnull=True,
    ).exclude(unit=target_unit, lease=target_lease, start_date=MOVE_DATE)
    for installation in active_installations:
        installation.end_date = max(OLD_END_DATE, installation.start_date)
        installation.end_reading = old_end_reading
        installation.is_active = False
        installation.active_meter_key = None
        installation.notes = (
            (installation.notes or "") + f"\nClosed by {REPAIR_REASON}."
        ).strip()
        installation.save(
            update_fields=[
                "end_date",
                "end_reading",
                "is_active",
                "active_meter_key",
                "notes",
                "updated_at",
            ]
        )

    start_reading = (
        _reading_value(MeterReading, meter, MOVE_DATE, after=True) or Decimal("0")
    )
    MeterInstallation.objects.update_or_create(
        meter=meter,
        unit=target_unit,
        lease=target_lease,
        start_date=MOVE_DATE,
        defaults={
            "start_reading": start_reading,
            "end_date": None,
            "end_reading": None,
            "is_active": True,
            "active_meter_key": meter.pk,
            "reason": REPAIR_REASON,
            "notes": "Linked F56 Flat #02 readings to its lease for monthly billing.",
        },
    )
    Meter.objects.filter(pk=meter.pk).update(unit=target_unit)

    MeterAssignmentHistory.objects.filter(
        meter=meter,
        new_unit=target_unit,
        change_date__date=MOVE_DATE,
    ).update(
        unit=target_unit,
        lease=target_lease,
        new_lease=target_lease,
    )


def reverse_repair(apps, schema_editor):
    Unit = apps.get_model("properties", "Unit")
    Meter = apps.get_model("smart_meter", "Meter")
    MeterAssignmentHistory = apps.get_model("smart_meter", "MeterAssignmentHistory")
    MeterInstallation = apps.get_model("smart_meter", "MeterInstallation")

    meter = Meter.objects.filter(meter_number=METER_NUMBER).first()
    old_unit = Unit.objects.filter(
        property__property_name="F35",
        unit_number="F35-FLAT# 04",
    ).first()
    if not meter:
        return

    MeterInstallation.objects.filter(
        meter=meter,
        reason=REPAIR_REASON,
    ).delete()

    old_installation = (
        MeterInstallation.objects.filter(meter=meter, unit=old_unit)
        .order_by("-start_date", "-id")
        .first()
        if old_unit
        else None
    )
    if old_installation:
        old_installation.end_date = None
        old_installation.end_reading = None
        old_installation.is_active = True
        old_installation.active_meter_key = meter.pk
        old_installation.save(
            update_fields=[
                "end_date",
                "end_reading",
                "is_active",
                "active_meter_key",
                "updated_at",
            ]
        )
        Meter.objects.filter(pk=meter.pk).update(unit=old_unit)
        MeterAssignmentHistory.objects.filter(
            meter=meter,
            new_unit__property__property_name="F56",
            new_unit__unit_number="F56-FLAT# 02",
            change_date__date=MOVE_DATE,
        ).update(
            lease=old_installation.lease,
            new_lease=old_installation.lease,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("smart_meter", "0017_repair_f54_f56_billing_links"),
    ]

    operations = [
        migrations.RunPython(repair_flat_2_meter_installation, reverse_repair),
    ]

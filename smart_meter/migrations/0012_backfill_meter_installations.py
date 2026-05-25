from datetime import date
from decimal import Decimal

from django.db import migrations


def _date_from_datetime(value):
    if not value:
        return None
    return value.date() if hasattr(value, "date") else value


def backfill_meter_installations(apps, schema_editor):
    Meter = apps.get_model("smart_meter", "Meter")
    MeterInstallation = apps.get_model("smart_meter", "MeterInstallation")
    Lease = apps.get_model("leases", "Lease")

    rows = []
    today = date.today()

    for meter in Meter.objects.filter(unit_id__isnull=False).iterator():
        if MeterInstallation.objects.filter(meter_id=meter.pk).exists():
            continue

        lease = (
            Lease.objects.filter(unit_id=meter.unit_id, status="active")
            .order_by("-start_date", "-id")
            .first()
        )

        start_date = (
            _date_from_datetime(getattr(meter, "installed_at", None))
            or getattr(lease, "start_date", None)
            or today
        )

        rows.append(
            MeterInstallation(
                meter_id=meter.pk,
                unit_id=meter.unit_id,
                lease_id=getattr(lease, "pk", None),
                start_date=start_date,
                start_reading=Decimal("0.000"),
                is_active=True,
                reason="backfill",
                notes="Backfilled from Meter.unit during meter installation history migration.",
            )
        )

    MeterInstallation.objects.bulk_create(rows, batch_size=500)


def reverse_backfill_meter_installations(apps, schema_editor):
    MeterInstallation = apps.get_model("smart_meter", "MeterInstallation")
    MeterInstallation.objects.filter(
        reason="backfill",
        notes="Backfilled from Meter.unit during meter installation history migration.",
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0039_backfill_lease_unit_occupancy"),
        ("smart_meter", "0011_meter_meter_type_alter_meter_unit_meterinstallation"),
    ]

    operations = [
        migrations.RunPython(backfill_meter_installations, reverse_backfill_meter_installations),
    ]

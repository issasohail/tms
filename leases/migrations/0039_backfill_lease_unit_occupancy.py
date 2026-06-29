from django.db import migrations


def backfill_occupancies(apps, schema_editor):
    Lease = apps.get_model("leases", "Lease")
    LeaseUnitOccupancy = apps.get_model("leases", "LeaseUnitOccupancy")

    rows = []
    for lease in Lease.objects.filter(unit_id__isnull=False).iterator():
        exists = LeaseUnitOccupancy.objects.filter(lease_id=lease.pk).exists()
        if exists:
            continue
        rows.append(
            LeaseUnitOccupancy(
                lease_id=lease.pk,
                unit_id=lease.unit_id,
                move_in_date=lease.start_date,
                move_out_date=None if lease.status == "active" else lease.end_date,
                notes="Backfilled from Lease.unit during occupancy history migration.",
            )
        )
    LeaseUnitOccupancy.objects.bulk_create(rows, batch_size=500)


def reverse_backfill_occupancies(apps, schema_editor):
    LeaseUnitOccupancy = apps.get_model("leases", "LeaseUnitOccupancy")
    LeaseUnitOccupancy.objects.filter(
        notes="Backfilled from Lease.unit during occupancy history migration."
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0038_leaseunitoccupancy"),
    ]

    operations = [
        migrations.RunPython(backfill_occupancies, reverse_backfill_occupancies),
    ]

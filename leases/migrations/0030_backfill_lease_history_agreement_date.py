from django.db import migrations


def backfill_agreement_dates(apps, schema_editor):
    LeaseRenewal = apps.get_model("leases", "LeaseRenewal")
    for history in LeaseRenewal.objects.filter(agreement_date__isnull=True).iterator():
        history.agreement_date = history.start_date
        history.save(update_fields=["agreement_date"])


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0029_leaserenewal_agreement_charges_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_agreement_dates, migrations.RunPython.noop),
    ]

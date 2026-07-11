import re
from django.db import migrations


def digits(value):
    return re.sub(r"\D", "", value or "")


def backfill_links(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    Lease = apps.get_model("leases", "Lease")
    Renewal = apps.get_model("leases", "LeaseRenewal")
    by_cnic = {digits(row.cnic): row.pk for row in Tenant.objects.exclude(cnic="") if digits(row.cnic)}
    for model in (Lease, Renewal):
        for row in model.objects.all().iterator():
            updates = []
            for number in (1, 2):
                link = f"witness{number}_tenant_id"
                cnic = getattr(row, f"witness{number}_cnic", "")
                if not getattr(row, link, None) and digits(cnic) in by_cnic:
                    setattr(row, link, by_cnic[digits(cnic)])
                    updates.append(link)
            if updates:
                row.save(update_fields=updates)


class Migration(migrations.Migration):
    dependencies = [("leases", "0070_lease_parties_authorized_occupants")]
    operations = [
        migrations.RunPython(backfill_links, migrations.RunPython.noop),
        migrations.RemoveField(model_name="lease", name="witness1_name"),
        migrations.RemoveField(model_name="lease", name="witness1_cnic"),
        migrations.RemoveField(model_name="lease", name="witness2_name"),
        migrations.RemoveField(model_name="lease", name="witness2_cnic"),
        migrations.RemoveField(model_name="leaserenewal", name="witness1_name"),
        migrations.RemoveField(model_name="leaserenewal", name="witness1_cnic"),
        migrations.RemoveField(model_name="leaserenewal", name="witness2_name"),
        migrations.RemoveField(model_name="leaserenewal", name="witness2_cnic"),
    ]

import re
from django.db import migrations


def digits(value):
    return re.sub(r"\D", "", value or "")


def backfill_links(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    Lease = apps.get_model("leases", "Lease")
    Renewal = apps.get_model("leases", "LeaseRenewal")
    by_cnic = {
        digits(cnic): pk
        for pk, cnic in Tenant.objects.exclude(cnic="").values_list("pk", "cnic")
        if digits(cnic)
    }
    fields = [
        "pk",
        "witness1_tenant_id", "witness1_cnic",
        "witness2_tenant_id", "witness2_cnic",
    ]
    for model in (Lease, Renewal):
        for row in model.objects.values(*fields).iterator():
            updates = {}
            for number in (1, 2):
                link = f"witness{number}_tenant_id"
                matched_id = by_cnic.get(digits(row.get(f"witness{number}_cnic")))
                if not row.get(link) and matched_id:
                    updates[link] = matched_id
            if updates:
                model.objects.filter(pk=row["pk"]).update(**updates)


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

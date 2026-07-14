import django.core.validators
from django.db import migrations, models


def set_every_lease_proration_to_seven(apps, schema_editor):
    Lease = apps.get_model("leases", "Lease")
    Lease.objects.all().update(proration_interval_days=7)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_proration_interval_integer_default_7"),
        ("tenants", "0023_tenantinteresttype_move_out_charges"),
        ("leases", "0076_lease_proration_interval_and_clause"),
    ]

    operations = [
        migrations.AlterField(
            model_name="lease",
            name="proration_interval_days",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Leave blank to use the system move-out proration interval.",
                null=True,
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
        migrations.RunPython(set_every_lease_proration_to_seven, migrations.RunPython.noop),
    ]

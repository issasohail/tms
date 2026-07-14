import django.core.validators
from django.db import migrations, models


def set_global_proration_to_seven(apps, schema_editor):
    GlobalSettings = apps.get_model("core", "GlobalSettings")
    GlobalSettings.objects.all().update(end_lease_proration_interval_days=7)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_globalsettings_end_lease_proration_interval_days"),
    ]

    operations = [
        migrations.AlterField(
            model_name="globalsettings",
            name="end_lease_proration_interval_days",
            field=models.PositiveSmallIntegerField(
                default=7,
                help_text="Default billing-day block used when monthly charges are prorated at move-out.",
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
        migrations.RunPython(set_global_proration_to_seven, migrations.RunPython.noop),
    ]

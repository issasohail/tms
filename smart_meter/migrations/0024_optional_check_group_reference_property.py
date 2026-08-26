import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("smart_meter", "0023_meter_timing_events"),
    ]

    operations = [
        migrations.AlterField(
            model_name="metercheckgroup",
            name="property",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Optional reference only; actual coverage comes from assigned billing meters."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="meter_check_groups",
                to="properties.property",
                verbose_name="Reference property",
            ),
        ),
    ]

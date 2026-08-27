from django.db import migrations, models


POWER_FIELDS = (
    "total_power",
    "power_a",
    "power_b",
    "power_c",
)


class Migration(migrations.Migration):
    dependencies = [
        ("smart_meter", "0028_bidirectional_three_phase_metering"),
    ]

    operations = [
        migrations.AlterField(
            model_name=model_name,
            name=field_name,
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                max_digits=10,
                null=True,
            ),
        )
        for model_name in ("livereading", "meterreading")
        for field_name in POWER_FIELDS
    ]

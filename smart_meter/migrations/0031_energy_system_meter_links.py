import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("smart_meter", "0030_expand_prepaid_parameter_1_settings")]

    operations = [
        migrations.CreateModel(
            name="EnergySystemMeterLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("side", models.CharField(choices=[("input", "Input / grid side"), ("output", "Output / load side")], max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("energy_system", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="meter_links", to="smart_meter.energysystem")),
                ("meter", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="energy_system_links", to="smart_meter.meter")),
            ],
            options={"ordering": ["side", "meter__meter_number"]},
        ),
        migrations.AddConstraint(
            model_name="energysystemmeterlink",
            constraint=models.UniqueConstraint(fields=("energy_system", "meter", "side"), name="unique_energy_system_meter_link"),
        ),
    ]

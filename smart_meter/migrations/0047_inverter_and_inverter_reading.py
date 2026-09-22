import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("smart_meter", "0046_metercheckgroup_automatic_coverage_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Inverter",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(help_text="e.g. 'Photon Inverter 1'", max_length=80)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("energy_system", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="inverters", to="smart_meter.energysystem")),
            ],
            options={
                "ordering": ["energy_system_id", "name"],
            },
        ),
        migrations.AddConstraint(
            model_name="inverter",
            constraint=models.UniqueConstraint(fields=("energy_system", "name"), name="unique_inverter_name_per_system"),
        ),
        migrations.CreateModel(
            name="InverterReading",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reading_kwh", models.DecimalField(decimal_places=3, max_digits=12)),
                ("recorded_at", models.DateTimeField()),
                ("screenshot", models.ImageField(blank=True, null=True, upload_to="inverter_readings/%Y/%m/")),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("inverter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="readings", to="smart_meter.inverter")),
            ],
            options={
                "ordering": ["-recorded_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="inverterreading",
            constraint=models.UniqueConstraint(fields=("inverter", "recorded_at"), name="unique_inverter_reading_timestamp"),
        ),
    ]

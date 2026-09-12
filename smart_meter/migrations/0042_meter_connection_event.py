from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("smart_meter", "0041_prepaid_parameter_baseline_and_batch"),
    ]

    operations = [
        migrations.CreateModel(
            name="MeterConnectionEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("meter_number", models.CharField(db_index=True, max_length=32)),
                ("event_type", models.CharField(choices=[("connected", "Connected"), ("reconnected", "Reconnected"), ("disconnected", "Disconnected"), ("rejected_duplicate", "Rejected older duplicate")], db_index=True, max_length=24)),
                ("occurred_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("source_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("source_port", models.PositiveIntegerField(blank=True, null=True)),
                ("previous_source_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("previous_source_port", models.PositiveIntegerField(blank=True, null=True)),
                ("connection_identity", models.CharField(blank=True, max_length=64)),
                ("connection_generation", models.BigIntegerField(blank=True, null=True)),
                ("connection_age_seconds", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True)),
                ("disconnect_reason", models.CharField(blank=True, max_length=32)),
            ],
            options={
                "ordering": ["-occurred_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="meterconnectionevent",
            index=models.Index(fields=["meter_number", "-occurred_at"], name="sm_conn_meter_time_idx"),
        ),
    ]

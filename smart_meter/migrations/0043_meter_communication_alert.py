from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("smart_meter", "0042_meter_connection_event")]

    operations = [
        migrations.CreateModel(
            name="MeterCommunicationAlert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("open", "Open"), ("resolved", "Resolved")], db_index=True, default="open", max_length=12)),
                ("threshold_minutes", models.PositiveIntegerField(default=30)),
                ("opened_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("last_reading_at", models.DateTimeField(db_index=True)),
                ("last_disconnect_at", models.DateTimeField(blank=True, null=True)),
                ("disconnect_reason", models.CharField(blank=True, max_length=32)),
                ("last_source_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("last_source_port", models.PositiveIntegerField(blank=True, null=True)),
                ("resolved_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("restored_reading_at", models.DateTimeField(blank=True, null=True)),
                ("offline_duration_seconds", models.PositiveBigIntegerField(blank=True, null=True)),
                ("meter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="communication_alerts", to="smart_meter.meter")),
            ],
            options={"ordering": ["-opened_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="metercommunicationalert",
            index=models.Index(fields=["meter", "status", "-opened_at"], name="sm_comm_alert_state_idx"),
        ),
    ]

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [("smart_meter", "0033_meter_reverse_energy_capability")]

    operations = [
        migrations.CreateModel(
            name="MeterRawFrame",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("received_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("source_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("source_port", models.PositiveIntegerField(blank=True, null=True)),
                ("control_code", models.PositiveSmallIntegerField()),
                ("data_identifier", models.CharField(blank=True, db_index=True, max_length=8)),
                ("data_length", models.PositiveSmallIntegerField(default=0)),
                ("raw_frame_hex", models.TextField()),
                ("checksum_style", models.CharField(blank=True, max_length=32)),
                ("decoded_data", models.JSONField(blank=True, default=dict)),
                ("trust_classification", models.CharField(choices=[("authoritative", "Authoritative direct register"), ("reported_unverified", "Meter-reported / unverified")], max_length=32)),
                ("parser_version", models.CharField(default="dlt645-v1", max_length=32)),
                ("meter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="raw_frames", to="smart_meter.meter")),
            ],
            options={"ordering": ["-received_at", "-id"]},
        ),
        migrations.AddIndex(model_name="meterrawframe", index=models.Index(fields=["meter", "received_at"], name="smart_meter_meter_i_80bb74_idx")),
        migrations.AddIndex(model_name="meterrawframe", index=models.Index(fields=["meter", "data_identifier", "received_at"], name="smart_meter_meter_i_d1be52_idx")),
    ]

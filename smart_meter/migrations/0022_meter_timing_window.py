import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("smart_meter", "0021_metercommand_meter_created_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="MeterTimingWindow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("weekday", models.PositiveSmallIntegerField(choices=[(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"), (4, "Friday"), (5, "Saturday"), (6, "Sunday")])),
                ("start_time", models.TimeField()),
                ("end_time", models.TimeField()),
                ("is_enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("meter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="timing_windows", to="smart_meter.meter")),
            ],
            options={
                "ordering": ["meter_id", "weekday", "start_time"],
            },
        ),
        migrations.AddConstraint(
            model_name="metertimingwindow",
            constraint=models.UniqueConstraint(fields=("meter", "weekday", "start_time", "end_time"), name="uniq_meter_timing_window"),
        ),
        migrations.AddIndex(
            model_name="metertimingwindow",
            index=models.Index(fields=["meter", "weekday", "is_enabled"], name="sm_timing_meter_day_idx"),
        ),
        migrations.AlterField(
            model_name="metercommand",
            name="source",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("credit_control", "Credit Control"),
                    ("payment", "Payment"),
                    ("prepaid", "Prepaid Pilot"),
                    ("system", "System"),
                    ("schedule", "Timing Schedule"),
                ],
                db_index=True,
                default="manual",
                max_length=24,
            ),
        ),
    ]

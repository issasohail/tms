from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("smart_meter", "0044_raw_frame_capture_policy")]

    operations = [
        migrations.AddField(
            model_name="meter",
            name="connection_event_capture_mode",
            field=models.CharField(
                choices=[
                    ("inherit", "Use global setting"),
                    ("all", "All connection events"),
                    ("off", "Off"),
                ],
                default="inherit",
                help_text="Override connection-event history for this meter.",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="meter",
            name="connection_event_capture_until",
            field=models.DateTimeField(
                blank=True,
                help_text="Optional expiry for All connection events; afterward the global setting is used.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="meter",
            name="connection_event_capture_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="metersettings",
            name="connection_event_capture_mode",
            field=models.CharField(
                choices=[
                    ("off", "Off (recommended)"),
                    ("all", "All connection events"),
                ],
                default="off",
                help_text="Default connection-event history for meters that inherit the global setting.",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="metersettings",
            name="connection_event_capture_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]

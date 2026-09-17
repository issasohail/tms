from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("smart_meter", "0043_meter_communication_alert"),
    ]

    operations = [
        migrations.AddField(
            model_name="meter",
            name="raw_frame_capture_mode",
            field=models.CharField(
                choices=[
                    ("inherit", "Use global setting"),
                    ("errors", "Errors only"),
                    ("all", "All frames"),
                    ("off", "Off"),
                ],
                default="inherit",
                help_text="Override raw-frame storage for this meter. Use global setting is recommended.",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="meter",
            name="raw_frame_capture_until",
            field=models.DateTimeField(
                blank=True,
                help_text="Optional expiry for an All frames override; after this time the global setting is used.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="meter",
            name="raw_frame_capture_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="metersettings",
            name="raw_frame_capture_mode",
            field=models.CharField(
                choices=[
                    ("errors", "Errors only (recommended)"),
                    ("all", "All frames"),
                    ("off", "Off"),
                ],
                default="errors",
                help_text="Default raw-frame storage for meters that inherit the global setting.",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="metersettings",
            name="raw_frame_capture_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]

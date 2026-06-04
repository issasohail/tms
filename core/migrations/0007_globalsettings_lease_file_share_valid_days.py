from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_globalsettings_enable_debug_toolbar"),
    ]

    operations = [
        migrations.AddField(
            model_name="globalsettings",
            name="lease_file_share_valid_days",
            field=models.PositiveIntegerField(
                default=7,
                help_text="Default number of days public lease file share links remain valid.",
            ),
        ),
    ]

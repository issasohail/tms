from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("smart_meter", "0020_alter_meter_unit_rate"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="metercommand",
            index=models.Index(
                fields=["meter", "-created_at"],
                name="sm_cmd_meter_created_idx",
            ),
        ),
    ]

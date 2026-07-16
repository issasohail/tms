from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("smart_meter", "0013_remove_meterinstallation_one_active_installation_per_meter_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="meter",
            name="meter_role",
            field=models.CharField(
                choices=[("billing", "Billing"), ("check", "Check / Audit")],
                default="billing",
                max_length=10,
            ),
        ),
    ]

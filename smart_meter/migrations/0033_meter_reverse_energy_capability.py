from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("smart_meter", "0032_auto_energy_poll_source")]

    operations = [
        migrations.AddField(
            model_name="meter",
            name="reverse_energy_capability",
            field=models.CharField(
                choices=[("unknown", "Not yet verified"), ("supported", "Supported"), ("not_supported", "Not supported")],
                default="unknown",
                help_text="Updated to Supported after a valid reverse-energy register response. Set Not supported only for meters known not to provide that register.",
                max_length=16,
            ),
        ),
    ]

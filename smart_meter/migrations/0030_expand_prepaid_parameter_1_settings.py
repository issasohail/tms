# Generated manually to preserve existing prepaid rows while adding the audited
# manufacturer Parameter 1 fields.  Existing fields retain their values.
from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("smart_meter", "0029_four_decimal_power_precision")]

    operations = [
        migrations.AddField(model_name="meterprepaidsettings", name="timezone_switch_time", field=models.BigIntegerField(default=0)),
        migrations.AddField(model_name="meterprepaidsettings", name="schedule_switch_time", field=models.BigIntegerField(default=0)),
        migrations.AddField(model_name="meterprepaidsettings", name="timezone_count", field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="meterprepaidsettings", name="schedule_count", field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="meterprepaidsettings", name="time_period_count", field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="meterprepaidsettings", name="rate_count", field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="meterprepaidsettings", name="step_count", field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="meterprepaidsettings", name="voltage_ratio", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="meterprepaidsettings", name="current_ratio", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="meterprepaidsettings", name="maximum_balance", field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
        migrations.AddField(model_name="meterprepaidsettings", name="reconnect_amount", field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
        migrations.AddField(model_name="meterprepaidsettings", name="max_load", field=models.DecimalField(decimal_places=4, default=Decimal("0.0000"), max_digits=10)),
        migrations.AddField(model_name="meterprepaidsettings", name="load_delay", field=models.PositiveSmallIntegerField(default=0)),
        *[migrations.AddField(model_name="meterprepaidsettings", name=f"rate{rate}_price_{slot}", field=models.DecimalField(decimal_places=4, default=Decimal("0.0000"), max_digits=10)) for rate in (1, 2) for slot in range(2, 5)],
        *[migrations.AddField(model_name="meterprepaidsettings", name=f"step{rate}_value_{slot}", field=models.DecimalField(decimal_places=4, default=Decimal("0.0000"), max_digits=12)) for rate in (1, 2) for slot in range(2, 4)],
        *[migrations.AddField(model_name="meterprepaidsettings", name=f"step{rate}_price_{slot}", field=models.DecimalField(decimal_places=4, default=Decimal("0.0000"), max_digits=10)) for rate in (1, 2) for slot in range(1, 5)],
    ]

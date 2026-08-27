from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("smart_meter", "0024_optional_check_group_reference_property"),
    ]

    operations = [
        migrations.AddField(
            model_name="meter",
            name="measurement_point",
            field=models.CharField(
                blank=True,
                choices=[
                    ("inverter_output", "Inverter Output"),
                    ("grid_interface", "Grid Interface"),
                    ("other_audit", "Other Audit"),
                ],
                default="",
                max_length=24,
            ),
        ),
        migrations.AddConstraint(
            model_name="meter",
            constraint=models.CheckConstraint(
                condition=models.Q(("meter_role", "check"), ("measurement_point", ""), _connector="OR"),
                name="billing_meter_measurement_point_blank",
            ),
        ),
    ]

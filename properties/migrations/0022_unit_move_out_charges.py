from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("properties", "0021_alter_property_caretaker_cnic_and_more")]

    operations = [
        migrations.AddField(
            model_name="unit",
            name="inspection_incomplete_charge",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("5000.00"),
                help_text="Move-out charge when the inspection sheet is not completed.",
                max_digits=10,
            ),
        ),
        migrations.AddField(
            model_name="unit",
            name="key_card_not_returned_charge",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("1000.00"),
                help_text="Move-out charge when keys/key cards are not recorded as returned.",
                max_digits=10,
            ),
        ),
    ]

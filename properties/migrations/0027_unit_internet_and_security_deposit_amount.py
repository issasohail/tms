from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0026_backfill_property_bank_accounts"),
    ]

    operations = [
        migrations.AddField(
            model_name="unit",
            name="internet_charges",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=10,
                null=True,
                verbose_name="Internet Charges",
            ),
        ),
        migrations.AddField(
            model_name="unit",
            name="security_deposit_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=10,
                null=True,
                verbose_name="Security Deposit Amount",
            ),
        ),
    ]

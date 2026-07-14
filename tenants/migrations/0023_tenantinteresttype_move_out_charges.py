from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0022_alter_pendingregistrationperson_cnic_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantinteresttype",
            name="inspection_incomplete_charge",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("5000.00"),
                help_text="Default move-out charge when inspection is not completed.",
                max_digits=10,
            ),
        ),
        migrations.AddField(
            model_name="tenantinteresttype",
            name="key_card_not_returned_charge",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("1000.00"),
                help_text="Default move-out charge when keys or key cards are not returned.",
                max_digits=10,
            ),
        ),
    ]

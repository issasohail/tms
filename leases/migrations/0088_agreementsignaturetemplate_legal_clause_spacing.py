from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0087_update_parking_clause_and_clause_15"),
    ]

    operations = [
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="legal_clause_spacing",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("5.00"),
                help_text="Maximum space, in points, inserted after each clause on Legal agreement pages.",
                max_digits=4,
                validators=[
                    MinValueValidator(Decimal("0.00")),
                    MaxValueValidator(Decimal("12.00")),
                ],
            ),
        ),
    ]

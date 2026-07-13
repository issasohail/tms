from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("leases", "0073_lease_party_relationships_and_declaration_defaults")]

    operations = [
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="legal_first_page_top_reserve",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("4.80"),
                help_text="Top blank area, in inches, on the first Legal agreement page.",
                max_digits=4,
                validators=[MinValueValidator(Decimal("0.50")), MaxValueValidator(Decimal("8.00"))],
            ),
        ),
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="legal_qr_reserve_width",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("4.00"),
                help_text="Width, in inches, of the QR/stamp reserve on the first Legal page.",
                max_digits=4,
                validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("7.50"))],
            ),
        ),
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="legal_qr_reserve_height",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("2.00"),
                help_text="Height, in inches, of the QR/stamp reserve on the first Legal page.",
                max_digits=4,
                validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("5.00"))],
            ),
        ),
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="legal_identity_bottom_reserve",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("3.10"),
                help_text="Bottom area, in inches, reserved for the four CNIC cards on Legal agreement page 2.",
                max_digits=4,
                validators=[MinValueValidator(Decimal("2.00")), MaxValueValidator(Decimal("5.00"))],
            ),
        ),
    ]

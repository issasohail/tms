from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


def set_standard_first_page_top_margin(apps, schema_editor):
    AgreementSignatureTemplate = apps.get_model(
        "leases", "AgreementSignatureTemplate"
    )
    AgreementSignatureTemplate.objects.update(
        legal_first_page_top_reserve=Decimal("0.55")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0090_qr_bottom_right_and_declaration_paragraphs"),
    ]

    operations = [
        migrations.AlterField(
            model_name="agreementsignaturetemplate",
            name="legal_first_page_top_reserve",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.55"),
                help_text="Top margin, in inches, on the first Legal agreement page.",
                max_digits=4,
                validators=[
                    MinValueValidator(Decimal("0.50")),
                    MaxValueValidator(Decimal("8.00")),
                ],
            ),
        ),
        migrations.RunPython(
            set_standard_first_page_top_margin,
            migrations.RunPython.noop,
        ),
    ]

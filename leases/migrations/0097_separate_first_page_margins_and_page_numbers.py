from decimal import Decimal

import django.core.validators
from django.db import migrations, models


def separate_existing_first_page_margin(apps, schema_editor):
    AgreementSignatureTemplate = apps.get_model(
        "leases", "AgreementSignatureTemplate"
    )
    for config in AgreementSignatureTemplate.objects.all():
        previous_margin = config.legal_first_page_top_reserve or Decimal("4.85")
        config.letter_first_page_top_reserve = previous_margin
        if previous_margin < Decimal("5.85"):
            config.legal_first_page_top_reserve = Decimal("5.85")
        config.save(
            update_fields=[
                "legal_first_page_top_reserve",
                "letter_first_page_top_reserve",
            ]
        )


def restore_shared_first_page_margin(apps, schema_editor):
    AgreementSignatureTemplate = apps.get_model(
        "leases", "AgreementSignatureTemplate"
    )
    for config in AgreementSignatureTemplate.objects.all():
        config.legal_first_page_top_reserve = (
            config.letter_first_page_top_reserve
        )
        config.save(update_fields=["legal_first_page_top_reserve"])


class Migration(migrations.Migration):
    dependencies = [
        ("leases", "0096_adjust_legal_estamp_footer_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="letter_first_page_top_reserve",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("4.85"),
                help_text=(
                    "Top margin, in inches, on the first stamped Letter "
                    "agreement page."
                ),
                max_digits=4,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0.50")),
                    django.core.validators.MaxValueValidator(Decimal("8.00")),
                ],
            ),
        ),
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="show_agreement_page_numbers",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Show the generated package page number at the bottom-right "
                    "of each PDF page."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="agreementsignaturetemplate",
            name="legal_first_page_top_reserve",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("5.85"),
                help_text=(
                    "Top margin, in inches, on the first stamped Legal "
                    "agreement page."
                ),
                max_digits=4,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0.50")),
                    django.core.validators.MaxValueValidator(Decimal("8.00")),
                ],
            ),
        ),
        migrations.RunPython(
            separate_existing_first_page_margin,
            restore_shared_first_page_margin,
        ),
    ]

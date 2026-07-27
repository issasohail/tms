from decimal import Decimal

import django.core.validators
from django.db import migrations, models


def copy_qr_settings_and_raise_letter_footer(apps, schema_editor):
    AgreementSignatureTemplate = apps.get_model(
        "leases", "AgreementSignatureTemplate"
    )
    for config in AgreementSignatureTemplate.objects.all():
        config.letter_qr_reserve_width = config.legal_qr_reserve_width
        config.letter_qr_reserve_height = max(
            config.legal_qr_reserve_height or Decimal("0"),
            Decimal("2.50"),
        )
        if config.estamp_letter_footer_bottom_points == 28:
            config.estamp_letter_footer_bottom_points = 44
        config.save(
            update_fields=[
                "letter_qr_reserve_width",
                "letter_qr_reserve_height",
                "estamp_letter_footer_bottom_points",
            ]
        )


def restore_previous_letter_values(apps, schema_editor):
    AgreementSignatureTemplate = apps.get_model(
        "leases", "AgreementSignatureTemplate"
    )
    AgreementSignatureTemplate.objects.filter(
        estamp_letter_footer_bottom_points=44
    ).update(estamp_letter_footer_bottom_points=28)


class Migration(migrations.Migration):
    dependencies = [
        ("leases", "0097_separate_first_page_margins_and_page_numbers"),
    ]

    operations = [
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="letter_qr_reserve_height",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("2.50"),
                help_text=(
                    "Height, in inches, of the QR/stamp reserve on the first "
                    "Letter page."
                ),
                max_digits=4,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0.00")),
                    django.core.validators.MaxValueValidator(Decimal("5.00")),
                ],
            ),
        ),
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="letter_qr_reserve_width",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("1.00"),
                help_text=(
                    "Width, in inches, of the QR/stamp reserve on the first "
                    "Letter page."
                ),
                max_digits=4,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0.00")),
                    django.core.validators.MaxValueValidator(Decimal("7.50")),
                ],
            ),
        ),
        migrations.AlterField(
            model_name="agreementsignaturetemplate",
            name="estamp_letter_footer_bottom_points",
            field=models.PositiveIntegerField(
                default=44,
                help_text=(
                    "Distance in PDF points between the bottom of a Letter page "
                    "and the saved E-Stamp QR/page-number footer. Increasing it "
                    "moves both upward."
                ),
                validators=[django.core.validators.MaxValueValidator(300)],
            ),
        ),
        migrations.RunPython(
            copy_qr_settings_and_raise_letter_footer,
            restore_previous_letter_values,
        ),
    ]

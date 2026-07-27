import django.core.validators
from django.db import migrations, models


def move_existing_default_footer_up_from_agreement_footer(apps, schema_editor):
    AgreementSignatureTemplate = apps.get_model(
        "leases", "AgreementSignatureTemplate"
    )
    AgreementSignatureTemplate.objects.filter(
        estamp_legal_footer_bottom_points=130
    ).update(estamp_legal_footer_bottom_points=46)


def restore_previous_default_footer_position(apps, schema_editor):
    AgreementSignatureTemplate = apps.get_model(
        "leases", "AgreementSignatureTemplate"
    )
    AgreementSignatureTemplate.objects.filter(
        estamp_legal_footer_bottom_points=46
    ).update(estamp_legal_footer_bottom_points=130)


class Migration(migrations.Migration):
    dependencies = [
        ("leases", "0095_agreement_footer_position_settings"),
    ]

    operations = [
        migrations.AlterField(
            model_name="agreementsignaturetemplate",
            name="estamp_legal_footer_bottom_points",
            field=models.PositiveIntegerField(
                default=46,
                help_text=(
                    "Distance in PDF points between the bottom of a Legal page "
                    "and the saved E-Stamp footer. The default keeps it just "
                    "above the agreement footer."
                ),
                validators=[django.core.validators.MaxValueValidator(300)],
            ),
        ),
        migrations.RunPython(
            move_existing_default_footer_up_from_agreement_footer,
            restore_previous_default_footer_position,
        ),
    ]

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("leases", "0094_make_vehicle_parking_clause_29_general"),
    ]

    operations = [
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="estamp_legal_footer_bottom_points",
            field=models.PositiveIntegerField(
                default=130,
                help_text=(
                    "Distance in PDF points between the bottom of a Legal page and "
                    "the saved E-Stamp footer. Increasing it moves the complete "
                    "E-Stamp upward."
                ),
                validators=[django.core.validators.MaxValueValidator(300)],
            ),
        ),
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="estamp_letter_footer_bottom_points",
            field=models.PositiveIntegerField(
                default=28,
                help_text=(
                    "Distance in PDF points between the bottom of a Letter page and "
                    "the saved E-Stamp footer. Increasing it moves the complete "
                    "E-Stamp upward."
                ),
                validators=[django.core.validators.MaxValueValidator(300)],
            ),
        ),
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="agreement_legal_footer_bottom_points",
            field=models.PositiveIntegerField(
                default=16,
                help_text=(
                    "Legal-page baseline position for the generated agreement "
                    "package footer."
                ),
                validators=[django.core.validators.MaxValueValidator(100)],
            ),
        ),
        migrations.AddField(
            model_name="agreementsignaturetemplate",
            name="agreement_letter_footer_bottom_points",
            field=models.PositiveIntegerField(
                default=16,
                help_text=(
                    "Letter-page baseline position for the generated agreement "
                    "package footer."
                ),
                validators=[django.core.validators.MaxValueValidator(100)],
            ),
        ),
    ]

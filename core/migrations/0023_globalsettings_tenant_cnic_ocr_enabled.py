from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0022_normalize_all_phone_country_codes"),
    ]

    operations = [
        migrations.AddField(
            model_name="globalsettings",
            name="tenant_cnic_ocr_enabled",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Allow authorized staff to read Pakistani CNIC front/back images "
                    "into a review screen. OCR never saves or overwrites fields automatically."
                ),
            ),
        ),
    ]

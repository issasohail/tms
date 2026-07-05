from django.db import migrations, models


LANGUAGE_CHOICES = [
    ("en", "English (en)"),
    ("en_US", "English - United States (en_US)"),
    ("en_GB", "English - United Kingdom (en_GB)"),
    ("ur", "Urdu (ur)"),
    ("ar", "Arabic (ar)"),
]


def update_utility_template_language_to_en(apps, schema_editor):
    WhatsAppUtilityTemplate = apps.get_model("whatsapp", "WhatsAppUtilityTemplate")
    WhatsAppUtilityTemplate.objects.filter(language_code__iexact="en_US").update(
        language_code="en"
    )


def reverse_utility_template_language_to_en_us(apps, schema_editor):
    WhatsAppUtilityTemplate = apps.get_model("whatsapp", "WhatsAppUtilityTemplate")
    WhatsAppUtilityTemplate.objects.filter(language_code="en").update(
        language_code="en_US"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("whatsapp", "0012_seed_meta_utility_templates"),
    ]

    operations = [
        migrations.AlterField(
            model_name="whatsapputilitytemplate",
            name="language_code",
            field=models.CharField(
                choices=LANGUAGE_CHOICES,
                default="en",
                max_length=20,
            ),
        ),
        migrations.RunPython(
            update_utility_template_language_to_en,
            reverse_utility_template_language_to_en_us,
        ),
    ]

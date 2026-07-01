from django.db import migrations


def seed_late_fee_reminder_template(apps, schema_editor):
    WhatsAppUtilityTemplate = apps.get_model("whatsapp", "WhatsAppUtilityTemplate")
    WhatsAppUtilityTemplate.objects.get_or_create(
        key="late_fee_reminder",
        defaults={
            "template_name": "late_fee_reminder",
            "language_code": "en",
            "body_text": (
                "Dear {{1}}, this is late payment reminder #{{3}} for invoice #{{2}}. "
                "Outstanding amount: {{4}}. Due date: {{5}}. Days overdue: {{6}}."
            ),
            "body_variables": [
                "Tenant name",
                "Invoice number",
                "Reminder number",
                "Outstanding amount",
                "Due date",
                "Days overdue",
            ],
            "button_label": "View invoice",
            "button_parameter_source": "Signed invoice token",
            "is_active": True,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("whatsapp", "0009_alter_whatsapputilitytemplate_key"),
    ]

    operations = [
        migrations.RunPython(seed_late_fee_reminder_template, migrations.RunPython.noop),
    ]

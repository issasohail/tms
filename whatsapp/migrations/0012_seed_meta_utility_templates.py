from django.db import migrations


UTILITY_TEMPLATES = [
    {
        "key": "invoice_notice",
        "template_name": "invoice_notice",
        "language_code": "en",
        "body_text": "Dear {{1}}, your invoice for {{2}} unit {{3}} is ready. Amount: {{4}}. Due date: {{5}}.",
        "body_variables": ["Tenant name", "Property", "Unit", "Invoice amount", "Due date"],
        "button_label": "View Invoice",
        "button_parameter_source": "Signed public invoice token",
        "notes": "Business-initiated invoice notice template used when the 24-hour WhatsApp session is closed.",
    },
    {
        "key": "payment_confirmation",
        "template_name": "payment_confirmation",
        "language_code": "en",
        "body_text": "Dear {{1}}, payment received for {{2}}. Amount: {{3}}. Receipt: {{4}}.",
        "body_variables": ["Tenant name", "Property / Unit", "Payment amount", "Receipt number"],
        "button_label": "View Receipt",
        "button_parameter_source": "Signed public payment receipt token",
        "notes": "Business-initiated payment confirmation template used when the 24-hour WhatsApp session is closed.",
    },
    {
        "key": "balance_reminder",
        "template_name": "balance_reminder",
        "language_code": "en",
        "body_text": "Dear {{1}},\n\nOur records show an outstanding balance.\n\nProperty / Unit:\n{{2}}\n\nOutstanding Balance:\nRs. {{3}}\nPlease Pay before {{4}} to avoid Late fees \nPlease review your account using the button below.\n\nThank you.",
        "body_variables": ["Tenant name", "Property / Unit", "Outstanding balance", "Due date"],
        "button_label": "View Ledger",
        "button_parameter_source": "WhatsAppExternalLinkToken ledger token",
        "notes": "Business-initiated outstanding balance reminder.",
    },
    {
        "key": "lease_ledger_link",
        "template_name": "lease_ledger_link",
        "language_code": "en",
        "body_text": "Dear {{1}}, your lease ledger for {{2}} is ready.",
        "body_variables": ["Tenant name", "Property / Unit"],
        "button_label": "Open Ledger",
        "button_parameter_source": "WhatsAppExternalLinkToken ledger token",
        "notes": "Business-initiated ledger link template.",
    },
    {
        "key": "rent_due_reminder",
        "template_name": "rent_due_reminder",
        "language_code": "en",
        "body_text": "Dear {{1}}, rent/payment for {{2}} is due. Amount: {{3}}. Due date: {{4}}.",
        "body_variables": ["Tenant name", "Property / Unit", "Amount due", "Due date"],
        "button_label": "Pay / View Invoice",
        "button_parameter_source": "Signed public invoice token",
        "notes": "Business-initiated rent due reminder.",
    },
    {
        "key": "late_fee_reminder",
        "template_name": "late_fee_reminder",
        "language_code": "en",
        "body_text": "Dear {{1}}, this is late payment reminder #{{3}} for invoice #{{2}}. Outstanding amount: {{4}}. Due date: {{5}}. Days overdue: {{6}}.",
        "body_variables": ["Tenant name", "Invoice number", "Reminder number", "Outstanding amount", "Due date", "Days overdue"],
        "button_label": "View invoice",
        "button_parameter_source": "Signed invoice token",
        "notes": "Business-initiated late fee reminder.",
    },
    {
        "key": "agreement_ready",
        "template_name": "agreement_ready",
        "language_code": "en",
        "body_text": "Dear {{1}}, your agreement for {{2}} is ready.",
        "body_variables": ["Tenant name", "Property / Unit"],
        "button_label": "Open Agreement",
        "button_parameter_source": "WhatsAppExternalLinkToken agreement token",
        "notes": "Business-initiated agreement ready template.",
    },
]


def seed_meta_utility_templates(apps, schema_editor):
    WhatsAppUtilityTemplate = apps.get_model("whatsapp", "WhatsAppUtilityTemplate")
    for item in UTILITY_TEMPLATES:
        defaults = {
            "template_name": item["template_name"],
            "language_code": item["language_code"],
            "body_text": item["body_text"],
            "body_variables": item["body_variables"],
            "button_label": item["button_label"],
            "button_parameter_source": item["button_parameter_source"],
            "is_active": True,
            "notes": item["notes"],
        }
        WhatsAppUtilityTemplate.objects.update_or_create(
            key=item["key"],
            defaults=defaults,
        )


def reverse_seed_meta_utility_templates(apps, schema_editor):
    # Keep rows in place on rollback. Deleting them could break message history/settings.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("whatsapp", "0011_alter_whatsappexternallinktoken_link_type"),
    ]

    operations = [
        migrations.RunPython(seed_meta_utility_templates, reverse_seed_meta_utility_templates),
    ]

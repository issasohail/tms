from django.db import migrations


def backfill_property_bank_accounts(apps, schema_editor):
    Property = apps.get_model("properties", "Property")
    PropertyBankAccount = apps.get_model("properties", "PropertyBankAccount")

    for property_obj in Property.objects.exclude(bank_account_details__isnull=True).iterator():
        details = (property_obj.bank_account_details or "").strip()
        if not details:
            continue
        PropertyBankAccount.objects.get_or_create(
            property_id=property_obj.pk,
            account_label="Primary Account",
            defaults={
                "additional_details": details,
                "is_default": True,
                "is_active": True,
                "sort_order": 10,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("properties", "0025_property_welcome_bank_account_mode_and_more"),
    ]

    operations = [
        migrations.RunPython(
            backfill_property_bank_accounts,
            migrations.RunPython.noop,
        ),
    ]

from django.db import migrations


def _category_for_key(key):
    if key.startswith("OWNER") or key.startswith("CARETAKER"):
        return "Owner"
    if key.startswith("TENANT"):
        return "Tenant"
    if key in {"UNIT_NUMBER", "PROPERTY_ADDRESS", "PROPERTY_NAME"}:
        return "Property"
    if "RENT" in key or "MAINTENANCE" in key or "TOTAL" in key or "DEPOSIT" in key or "FEE" in key or "CHARGES" in key:
        return "Financial"
    if "DATE" in key or "DURATION" in key:
        return "Dates"
    if "WITNESS" in key:
        return "Witness"
    if "METER" in key or "INVENTORY" in key or "KEY" in key:
        return "Condition"
    return "General"


def seed_placeholders(apps, schema_editor):
    AgreementPlaceholder = apps.get_model("leases", "AgreementPlaceholder")
    from leases.models import PLACEHOLDER_REGISTRY

    for index, key in enumerate(sorted(PLACEHOLDER_REGISTRY.keys()), start=1):
        AgreementPlaceholder.objects.update_or_create(
            key=key,
            defaults={
                "label": key.replace("_", " ").title(),
                "description": f"System placeholder [{key}]",
                "category": _category_for_key(key),
                "source_type": "system",
                "resolver_key": key,
                "is_active": True,
                "sort_order": index,
            },
        )

    custom_defaults = [
        ("BANK_ACCOUNT", "Bank Account", "Custom payment/bank account text", "Custom"),
        ("POLICE_REGISTRATION_NOTE", "Police Registration Note", "Custom police registration note", "Custom"),
        ("REPAIR_REPORTING_INSTRUCTION", "Repair Reporting Instruction", "Custom repair reporting instructions", "Custom"),
        ("CUSTOM_RULE_1", "Custom Rule 1", "Custom agreement rule", "Custom"),
    ]
    base_order = len(PLACEHOLDER_REGISTRY) + 1
    for offset, (key, label, description, category) in enumerate(custom_defaults):
        AgreementPlaceholder.objects.get_or_create(
            key=key,
            defaults={
                "label": label,
                "description": description,
                "category": category,
                "source_type": "custom",
                "is_active": True,
                "sort_order": base_order + offset,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0033_agreementplaceholder"),
    ]

    operations = [
        migrations.RunPython(seed_placeholders, migrations.RunPython.noop),
    ]

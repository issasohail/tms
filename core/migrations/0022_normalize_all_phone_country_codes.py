import re

from django.db import migrations

from core.model_fields import NormalizedPhoneField


NON_DIGITS_RE = re.compile(r"\D+")


def _normalize_phone(value, country_digits):
    raw = str(value or "").strip()
    if not raw:
        return ""

    digits = NON_DIGITS_RE.sub("", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        return f"+{digits}"
    if digits.startswith("0") and not digits.startswith("00"):
        return f"+{country_digits}{digits[1:]}"
    return digits


def normalize_all_phone_country_codes(apps, schema_editor):
    GlobalSettings = apps.get_model("core", "GlobalSettings")
    settings_obj = GlobalSettings.objects.filter(pk=1).first()
    configured_code = getattr(settings_obj, "country_code", "") if settings_obj else ""
    country_digits = NON_DIGITS_RE.sub("", str(configured_code or ""))
    if not country_digits or not country_digits.strip("0"):
        country_digits = "92"

    for Model in apps.get_models():
        phone_fields = [
            field
            for field in Model._meta.concrete_fields
            if isinstance(field, NormalizedPhoneField)
        ]
        if not phone_fields:
            continue

        field_names = [field.attname for field in phone_fields]
        rows = Model._default_manager.values_list("pk", *field_names).iterator()
        for row in rows:
            object_pk, *current_values = row
            updates = {}
            for field, current_value in zip(phone_fields, current_values):
                if current_value in (None, ""):
                    continue
                normalized_value = _normalize_phone(current_value, country_digits)
                if normalized_value != current_value:
                    updates[field.attname] = normalized_value
            if updates:
                Model._default_manager.filter(pk=object_pk).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_alter_account_whatsapp_number"),
        ("core", "0021_normalize_pending_approval_phone_prefixes"),
        ("handyman", "0004_alter_handymanprofile_phone_and_more"),
        ("leases", "0092_update_renewal_maintenance_inspection_clauses"),
        ("properties", "0027_unit_internet_and_security_deposit_amount"),
        ("tenants", "0025_registration_onboarding_safety"),
        ("whatsapp", "0018_pendingwhatsappmedia_processing"),
    ]

    operations = [
        migrations.RunPython(
            normalize_all_phone_country_codes,
            migrations.RunPython.noop,
        ),
    ]

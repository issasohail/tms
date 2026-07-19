import re

from django.db import migrations


NON_DIGITS_RE = re.compile(r"\D+")


def _international_phone(value, country_digits):
    raw = str(value or "").strip()
    digits = NON_DIGITS_RE.sub("", raw)
    if (
        not raw
        or raw.startswith("+")
        or not country_digits
        or len(digits) < 10
        or not digits.startswith("0")
        or digits.startswith("00")
    ):
        return raw
    return f"+{country_digits}{digits[1:]}"


def normalize_pending_approval_phones(apps, schema_editor):
    GlobalSettings = apps.get_model("core", "GlobalSettings")
    settings_obj = GlobalSettings.objects.filter(pk=1).first()
    configured_code = getattr(settings_obj, "country_code", "") if settings_obj else ""
    country_digits = NON_DIGITS_RE.sub("", str(configured_code or ""))
    if not country_digits or not country_digits.strip("0"):
        country_digits = "92"

    targets = (
        ("tenants", "Tenant", ("phone",)),
        ("tenants", "PendingRegistrationPerson", ("phone",)),
        ("leases", "PendingLeaseFamilyMemberSubmission", ("phone",)),
        ("leases", "PendingPoliceVerificationSubmission", ("phone",)),
        ("whatsapp", "PendingWhatsAppPayment", ("phone",)),
        ("whatsapp", "PendingWhatsAppMedia", ("phone",)),
        ("whatsapp", "PendingWhatsAppMaintenance", ("phone",)),
    )

    for app_label, model_name, fields in targets:
        Model = apps.get_model(app_label, model_name)
        for item in Model.objects.only("pk", *fields).iterator():
            updates = {}
            for field_name in fields:
                old_value = getattr(item, field_name, "") or ""
                new_value = _international_phone(old_value, country_digits)
                if new_value != old_value:
                    updates[field_name] = new_value
            if updates:
                Model.objects.filter(pk=item.pk).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_globalsettings_default_motorcycle_parking_rate_and_more"),
        ("leases", "0091_first_legal_page_standard_top_margin"),
        ("tenants", "0024_link_interest_to_building_type"),
        ("whatsapp", "0017_seed_tenant_simulator_staff_access"),
    ]

    operations = [
        migrations.RunPython(
            normalize_pending_approval_phones,
            migrations.RunPython.noop,
        ),
    ]

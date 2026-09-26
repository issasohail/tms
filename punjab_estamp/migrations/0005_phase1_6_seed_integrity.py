from django.db import migrations


ESTAMP_CODE = "estamp_paper"
DEFAULT_PURPOSE_PORTAL_VALUE = "208"


def ensure_phase1_6_seed_integrity(apps, schema_editor):
    """Repair only static Phase 1-6 configuration that may be missing.

    Punjab District/Tehsil/Relation rows are already guaranteed by
    punjab_estamp.0003 and the Phase 7 production-cache migration 0004.
    This migration covers the remaining cross-app static rows used by the
    Punjab workflow without overwriting administrator customizations.
    """

    LeaseDocumentCategory = apps.get_model("leases", "LeaseDocumentCategory")
    Purpose = apps.get_model("punjab_estamp", "PunjabEStampPurpose")

    LeaseDocumentCategory.objects.get_or_create(
        code=ESTAMP_CODE,
        defaults={
            "name": "E-Stamp Paper",
            "is_active": True,
            "sort_order": 55,
        },
    )

    purpose, _created = Purpose.objects.get_or_create(
        portal_value=DEFAULT_PURPOSE_PORTAL_VALUE,
        defaults={
            "name": "AGREEMENT OR MEMORANDUM OF AN AGREEMENT - 5(ccc)",
            "denomination": 100,
            "default_continuation_sheets": 1,
            "active": True,
            "is_default_for_lease": True,
            "sort_order": 1,
        },
    )

    # Preserve an administrator-selected default. Only repair the verified
    # purpose when the database currently has no active default at all.
    if not Purpose.objects.filter(active=True, is_default_for_lease=True).exists():
        updates = []
        if not purpose.active:
            purpose.active = True
            updates.append("active")
        if not purpose.is_default_for_lease:
            purpose.is_default_for_lease = True
            updates.append("is_default_for_lease")
        if updates:
            purpose.save(update_fields=updates)


class Migration(migrations.Migration):
    dependencies = [
        ("leases", "0093_estamp_category_settings_permission"),
        ("punjab_estamp", "0004_phase7_verified_production_seed"),
    ]

    operations = [
        migrations.RunPython(
            ensure_phase1_6_seed_integrity,
            migrations.RunPython.noop,
        ),
    ]

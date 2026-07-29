import re

from django.conf import settings
from django.db import migrations
from django.utils import timezone
from django.utils.dateparse import parse_date


MIN_CONFIDENCE = 80

TEXT_FIELDS = (
    "country",
    "temporary_address",
    "permanent_address",
    "temporary_address_urdu",
    "permanent_address_urdu",
)

DATE_FIELDS = (
    "date_of_birth",
    "cnic_issue_date",
    "cnic_expiry_date",
)


def _cnic_digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _is_blank(value):
    return value is None or str(value).strip() == ""


def backfill_active_lease_cnic_details(apps, schema_editor):
    """
    Read saved CNIC images for active-lease tenants and family members.

    OCR is deliberately conservative:
    - both saved CNIC images are required;
    - the detected CNIC must match the saved CNIC when both are readable;
    - only blank fields are populated;
    - failures are reported and skipped so one bad image does not block deploy.
    """
    if not getattr(settings, "OPENAI_API_KEY", ""):
        raise RuntimeError(
            "OPENAI_API_KEY must be configured before running tenant migration 0029."
        )

    from tenants.services.cnic_ocr import extract_cnic_identity
    from whatsapp.services.ai_config import get_whatsapp_ai_config

    Tenant = apps.get_model("tenants", "Tenant")
    Lease = apps.get_model("leases", "Lease")
    LeaseFamilyMember = apps.get_model("leases", "LeaseFamilyMember")
    database = schema_editor.connection.alias

    active_leases = Lease.objects.using(database).filter(status="active")
    tenant_ids = set(
        active_leases.values_list("tenant_id", flat=True)
    )
    tenant_ids.update(
        LeaseFamilyMember.objects.using(database)
        .filter(lease__status="active")
        .values_list("family_member_id", flat=True)
    )

    model_name = get_whatsapp_ai_config().model
    totals = {
        "found": len(tenant_ids),
        "updated": 0,
        "unchanged": 0,
        "missing_images": 0,
        "mismatch": 0,
        "low_confidence": 0,
        "failed": 0,
    }

    print(
        "CNIC OCR backfill: processing "
        f"{totals['found']} distinct active-lease tenant/family record(s)."
    )

    tenants = (
        Tenant.objects.using(database)
        .filter(pk__in=tenant_ids)
        .order_by("pk")
    )
    for tenant in tenants.iterator(chunk_size=25):
        if not tenant.cnic_front or not tenant.cnic_back:
            totals["missing_images"] += 1
            print(
                f"CNIC OCR backfill: tenant {tenant.pk} skipped; "
                "front and back images are both required."
            )
            continue

        try:
            result = extract_cnic_identity(
                tenant.cnic_front,
                tenant.cnic_back,
                model_name,
            )
        except Exception as exc:
            totals["failed"] += 1
            print(
                f"CNIC OCR backfill: tenant {tenant.pk} failed with "
                f"{exc.__class__.__name__}."
            )
            continue

        fields = result.get("fields") or {}
        if not fields:
            totals["failed"] += 1
            print(
                f"CNIC OCR backfill: tenant {tenant.pk} was not updated; "
                f"{result.get('message') or 'no identity fields were detected'}."
            )
            continue

        confidence = int(result.get("confidence") or 0)
        if confidence < MIN_CONFIDENCE:
            totals["low_confidence"] += 1
            print(
                f"CNIC OCR backfill: tenant {tenant.pk} skipped; "
                f"confidence {confidence}% is below {MIN_CONFIDENCE}%."
            )
            continue

        saved_cnic = _cnic_digits(tenant.cnic)
        detected_cnic = _cnic_digits(fields.get("cnic"))
        if saved_cnic and detected_cnic and saved_cnic != detected_cnic:
            totals["mismatch"] += 1
            print(
                f"CNIC OCR backfill: tenant {tenant.pk} skipped; "
                "the detected CNIC does not match the saved CNIC."
            )
            continue

        updates = {}
        for field_name in TEXT_FIELDS:
            incoming = str(fields.get(field_name) or "").strip()
            if incoming and _is_blank(getattr(tenant, field_name, None)):
                updates[field_name] = incoming

        for field_name in DATE_FIELDS:
            incoming = parse_date(str(fields.get(field_name) or ""))
            if incoming and getattr(tenant, field_name, None) is None:
                updates[field_name] = incoming

        detected_gender = str(fields.get("gender") or "").strip()
        if (
            detected_gender in {"M", "F", "O"}
            and _is_blank(getattr(tenant, "gender", None))
        ):
            updates["gender"] = detected_gender

        if not saved_cnic and len(detected_cnic) == 13:
            duplicate_exists = (
                Tenant.objects.using(database)
                .exclude(pk=tenant.pk)
                .filter(cnic_digits=detected_cnic)
                .exists()
            )
            if not duplicate_exists:
                updates["cnic"] = (
                    f"{detected_cnic[:5]}-{detected_cnic[5:12]}-"
                    f"{detected_cnic[12]}"
                )
                updates["cnic_digits"] = detected_cnic

        if not updates:
            totals["unchanged"] += 1
            continue

        updates["updated_at"] = timezone.now()
        Tenant.objects.using(database).filter(pk=tenant.pk).update(**updates)
        totals["updated"] += 1
        print(
            f"CNIC OCR backfill: tenant {tenant.pk} updated fields: "
            f"{', '.join(sorted(key for key in updates if key != 'updated_at'))}."
        )

    print(
        "CNIC OCR backfill complete: "
        + ", ".join(f"{key}={value}" for key, value in totals.items())
        + "."
    )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("tenants", "0028_tenant_monthly_income_bracket"),
        ("leases", "0099_leaserenewal_photo_settings"),
    ]

    operations = [
        migrations.RunPython(
            backfill_active_lease_cnic_details,
            migrations.RunPython.noop,
        ),
    ]

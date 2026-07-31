import os
from datetime import timedelta

from django.core.files.base import ContentFile
from django.db import transaction
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from core.models import GlobalSettings
from leases.models import LeaseDocument, LeaseDocumentCategory, PendingPoliceVerificationSubmission
from tenants.models import TenantRegistrationSubmission
from whatsapp.models import WhatsAppExternalLinkToken


def police_category_code():
    settings_obj = GlobalSettings.get_solo()
    return settings_obj.police_verification_document_category_code or "police_verification"


def police_link_valid_hours():
    settings_obj = GlobalSettings.get_solo()
    return settings_obj.police_verification_link_valid_hours or 48


def police_whatsapp_command():
    settings_obj = GlobalSettings.get_solo()
    return settings_obj.police_verification_whatsapp_command or "Police Verification"


def ensure_police_category():
    code = police_category_code()
    category, _ = LeaseDocumentCategory.objects.get_or_create(
        code=code,
        defaults={"name": "Police Verification", "sort_order": 30, "is_active": True},
    )
    return category


def create_police_verification_link(request, lease, created_by=None, phone_number=""):
    expires_at = timezone.now() + timedelta(hours=police_link_valid_hours())
    link = WhatsAppExternalLinkToken.objects.create(
        link_type=WhatsAppExternalLinkToken.LINK_POLICE_VERIFICATION,
        phone_number=phone_number or getattr(lease.tenant, "phone", "") or "",
        tenant=lease.tenant,
        staff_user=created_by,
        target_app_label="leases",
        target_model="Lease",
        target_object_id=lease.pk,
        metadata={"purpose": "police_verification"},
        expires_at=expires_at,
    )
    path = reverse("leases:public_police_verification", args=[link.token])
    if request is not None:
        return link, request.build_absolute_uri(path)
    base_url = (getattr(settings, "WHATSAPP_PUBLIC_BASE_URL", "") or "https://kirayas.com").rstrip("/")
    return link, f"{base_url}{path}"


def get_valid_police_link(token):
    link = WhatsAppExternalLinkToken.objects.filter(
        token=token,
        link_type=WhatsAppExternalLinkToken.LINK_POLICE_VERIFICATION,
        is_active=True,
    ).first()
    if not link or not link.is_valid or not link.target_object_id:
        return None, link
    from leases.models import Lease

    lease = Lease.objects.select_related("tenant", "unit__property").filter(pk=link.target_object_id).first()
    return lease, link


def field_status(label, value):
    return {"label": label, "value": value, "missing": not bool(value)}


def police_context_sections(lease):
    tenant = lease.tenant
    unit = lease.unit
    property_obj = getattr(unit, "property", None)
    owner = getattr(property_obj, "owner", None)

    owner_fields = [
        field_status("Owner name", getattr(owner, "name", "") or getattr(owner, "full_name", "") or str(owner or "")),
        field_status("Father name", getattr(owner, "father_name", "")),
        field_status("CNIC", getattr(owner, "cnic", "")),
        field_status("Phone", getattr(owner, "phone", "")),
        field_status("Address", getattr(owner, "address", "")),
        field_status("Photo", getattr(property_obj, "owner_photo", None)),
    ]
    property_fields = [
        field_status("House #", getattr(property_obj, "house_no", "")),
        field_status("Street #", getattr(property_obj, "street_no", "")),
        field_status("Colony", getattr(property_obj, "colony", "")),
        field_status("Road", getattr(property_obj, "road", "")),
        field_status("Covered Area Type", getattr(property_obj, "covered_area_type", "")),
        field_status("Police Station", getattr(property_obj, "police_station", "")),
        field_status("Division", getattr(property_obj, "police_division", "")),
        field_status("Circle", getattr(property_obj, "police_circle", "")),
        field_status("Zila", getattr(property_obj, "zila", "")),
    ]
    tenant_fields = [
        field_status("Tenant name", tenant.get_full_name() if tenant else ""),
        field_status("CNIC", getattr(tenant, "cnic", "")),
        field_status("Phone", getattr(tenant, "phone", "")),
        field_status("Occupation", getattr(tenant, "occupation", "")),
        field_status("Permanent address", getattr(tenant, "permanent_address", "")),
        field_status("Temporary address", getattr(tenant, "temporary_address", "")),
        field_status("Adult family members", getattr(tenant, "family_member_adults", None)),
        field_status("Children", getattr(tenant, "family_member_children", None)),
        field_status("NADRA family #", getattr(tenant, "nadra_family_no", "")),
    ]
    vehicles = lease.vehicles.filter(is_active=True).select_related("vehicle_type").order_by(
        "vehicle_type__sort_order", "registration_number"
    )
    vehicle_fields = [
        field_status(
            "Vehicle information",
            ", ".join(
                f"{vehicle.vehicle_type.name} {vehicle.registration_number}"
                for vehicle in vehicles
            ),
        )
    ]
    return {
        "owner_fields": owner_fields,
        "property_fields": property_fields,
        "tenant_fields": tenant_fields,
        "vehicle_fields": vehicle_fields,
        "vehicles": vehicles,
        "missing_count": sum(1 for section in (owner_fields, property_fields, tenant_fields, vehicle_fields) for row in section if row["missing"]),
    }


def build_police_whatsapp_message(request, lease, url):
    context = police_context_sections(lease)
    missing = [
        row["label"]
        for section_name in ("owner_fields", "property_fields", "tenant_fields", "vehicle_fields")
        for row in context[section_name]
        if row["missing"]
    ]
    lines = [
        "Police verification link:",
        url,
        "",
        f"Property/Unit: {lease.unit.property.property_name} / {lease.unit.unit_number}",
    ]
    if missing:
        lines.extend(["", "Please fill missing fields:", ", ".join(missing[:12])])
    lines.extend(["", "You can also send the final police verification PDF/image here after this message."])
    return "\n".join(lines)


def create_tenant_registration_submission(lease, data, files=None):
    tenant = lease.tenant
    files = files or {}
    if not tenant or not (data or any(files.values())):
        return None
    return TenantRegistrationSubmission.objects.create(
        tenant=tenant,
        submitted_data=data,
        photo=files.get("photo"),
        cnic_front=files.get("cnic_front"),
        cnic_back=files.get("cnic_back"),
    )


def create_pending_police_submission(lease, uploaded_file, source, phone="", notes="", tenant=None, whatsapp_media=None):
    return PendingPoliceVerificationSubmission.objects.create(
        lease=lease,
        tenant=tenant or lease.tenant,
        file=uploaded_file,
        original_filename=os.path.basename(getattr(uploaded_file, "name", "") or ""),
        source=source,
        phone=phone,
        notes=notes,
        whatsapp_media=whatsapp_media,
    )


@transaction.atomic
def approve_police_submission(submission, user):
    if submission.status != PendingPoliceVerificationSubmission.STATUS_PENDING:
        raise ValueError("This police verification submission has already been reviewed.")
    if not submission.file:
        raise ValueError("No police verification file is attached.")

    ensure_police_category()
    submission.file.open("rb")
    content = ContentFile(submission.file.read(), name=submission.original_filename or os.path.basename(submission.file.name))
    submission.file.close()

    document = LeaseDocument.objects.create(
        lease=submission.lease,
        file=content,
        original_filename=submission.original_filename,
        display_name=submission.original_filename or "Police Verification",
        category=police_category_code(),
        description=f"Approved from {submission.get_source_display()} submission #{submission.pk}. {submission.notes}".strip(),
        uploaded_by=user,
    )
    lease = submission.lease
    lease.police_verification_status = "verified"
    lease.police_verification_date = timezone.localdate()
    lease.police_verified_by = user
    lease.police_verification_document = document.file
    lease.save(update_fields=[
        "police_verification_status",
        "police_verification_date",
        "police_verified_by",
        "police_verification_document",
        "updated_at",
    ])
    submission.status = PendingPoliceVerificationSubmission.STATUS_APPROVED
    submission.reviewed_by = user
    submission.reviewed_at = timezone.now()
    submission.approved_document = document
    submission.save(update_fields=["status", "reviewed_by", "reviewed_at", "approved_document"])
    return document

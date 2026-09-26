from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.utils.identity import normalize_cnic
from leases.whatsapp import build_whatsapp_url
from punjab_estamp.models import (
    LeaseEStampWorkflow,
    PunjabEStampPurpose,
    PunjabEStampRelation,
)


def _text(value):
    return str(value or "").strip()


def _normalized_relation(value):
    return "".join(_text(value).upper().replace(".", "").split())


def resolve_property_relation(value):
    normalized = _normalized_relation(value)
    if not normalized:
        return None
    for relation in PunjabEStampRelation.objects.all():
        if _normalized_relation(relation.name) == normalized:
            return relation
    return None


def default_lease_purpose():
    return (
        PunjabEStampPurpose.objects.filter(
            active=True,
            is_default_for_lease=True,
        )
        .order_by("sort_order", "pk")
        .first()
    )


def workflow_comment(lease):
    property_name = _text(lease.unit.property.property_name)
    unit_number = _text(lease.unit.unit_number)
    unit_name = " ".join(part for part in (property_name, unit_number) if part)
    tenant_name = _text(lease.tenant.get_full_name())
    return f"{unit_name} - {tenant_name}"


def _linked_applicant(tenant, source):
    relation = tenant.relation if tenant.relation_id else None
    return {
        "source": source,
        "source_label": "Caretaker" if source == "linked_caretaker" else "Owner",
        "record_id": tenant.pk,
        "name": _text(tenant.first_name),
        "cnic": _text(tenant.cnic),
        "phone": _text(tenant.phone),
        "email": _text(tenant.email),
        "address": _text(
            tenant.permanent_address or tenant.address or tenant.temporary_address
        ),
        "relation_name": _text(relation.name if relation else tenant.relation_legacy),
        "relation_portal_value": _text(relation.portal_value if relation else ""),
        "relation_person_name": _text(tenant.last_name),
        "relation_id": relation.pk if relation else None,
        "relation_active": bool(relation and relation.active),
    }


def _legacy_applicant(property_obj, source):
    caretaker = source == "legacy_caretaker"
    prefix = "caretaker" if caretaker else "owner"
    relation_text = getattr(
        property_obj,
        "caretaker_relation" if caretaker else "relation",
        "",
    )
    relation = resolve_property_relation(relation_text)
    return {
        "source": source,
        "source_label": "Caretaker" if caretaker else "Owner",
        "record_id": None,
        "name": _text(getattr(property_obj, f"{prefix}_name", "")),
        "cnic": _text(getattr(property_obj, f"{prefix}_cnic", "")),
        "phone": _text(getattr(property_obj, f"{prefix}_phone", "")),
        "email": "",
        "address": _text(getattr(property_obj, f"{prefix}_address", "")),
        "relation_name": _text(relation.name if relation else relation_text),
        "relation_portal_value": _text(relation.portal_value if relation else ""),
        "relation_person_name": _text(
            getattr(property_obj, f"{prefix}_father_name", "")
        ),
        "relation_id": relation.pk if relation else None,
        "relation_active": bool(relation and relation.active),
    }


def applicant_snapshot(lease):
    property_obj = lease.unit.property
    if property_obj.caretaker_tenant_id:
        return _linked_applicant(property_obj.caretaker_tenant, "linked_caretaker")
    if any(
        _text(value)
        for value in (
            property_obj.caretaker_name,
            property_obj.caretaker_cnic,
            property_obj.caretaker_phone,
            property_obj.caretaker_address,
        )
    ):
        return _legacy_applicant(property_obj, "legacy_caretaker")
    if property_obj.owner_tenant_id:
        return _linked_applicant(property_obj.owner_tenant, "linked_owner")
    return _legacy_applicant(property_obj, "legacy_owner")


def snapshot_components(lease, purpose=None):
    property_obj = lease.unit.property
    purpose = purpose or default_lease_purpose()
    district = property_obj.zila
    tehsil = property_obj.tehsil
    applicant = applicant_snapshot(lease)
    property_data = {
        "property_id": property_obj.pk,
        "property_name": _text(property_obj.property_name),
        "unit_id": lease.unit_id,
        "unit_name": " ".join(
            part
            for part in (
                _text(property_obj.property_name),
                _text(lease.unit.unit_number),
            )
            if part
        ),
        "unit_number": _text(lease.unit.unit_number),
        "district_name": _text(district.name if district else ""),
        "district_portal_value": _text(
            district.portal_value if district else ""
        ),
        "district_id": district.pk if district else None,
        "tehsil_name": _text(tehsil.name if tehsil else ""),
        "tehsil_portal_value": _text(tehsil.portal_value if tehsil else ""),
        "tehsil_id": tehsil.pk if tehsil else None,
    }
    portal_data = {
        "purpose_name": _text(purpose.name if purpose else ""),
        "purpose_portal_value": _text(purpose.portal_value if purpose else ""),
        "purpose_id": purpose.pk if purpose else None,
        "denomination": purpose.denomination if purpose else None,
        "continuation_sheets": (
            purpose.default_continuation_sheets if purpose else None
        ),
        "reason": workflow_comment(lease),
        "comment": workflow_comment(lease),
    }
    return applicant, property_data, portal_data


def missing_configuration(lease, purpose=None):
    property_obj = lease.unit.property
    purpose = purpose or default_lease_purpose()
    applicant, _property_data, _portal_data = snapshot_components(lease, purpose)
    missing = []

    if not property_obj.zila_id:
        missing.append("Property Zila / District")
    elif not property_obj.zila.active or not _text(property_obj.zila.portal_value):
        missing.append("active Punjab mapping for Property Zila / District")
    if not property_obj.tehsil_id:
        missing.append("Property Tehsil")
    elif property_obj.zila_id and property_obj.tehsil.district_id != property_obj.zila_id:
        missing.append("Tehsil belonging to the selected Zila / District")
    elif not property_obj.tehsil.active or not _text(property_obj.tehsil.portal_value):
        missing.append("active Punjab mapping for Property Tehsil")

    if not applicant.get("name"):
        missing.append("Applicant Name")
    if len(normalize_cnic(applicant.get("cnic"))) != 13:
        missing.append("Applicant CNIC")
    if not applicant.get("phone"):
        missing.append("Applicant Phone")
    if not applicant.get("relation_id") or not applicant.get("relation_active") or not applicant.get("relation_portal_value"):
        missing.append("active Punjab Applicant Relation mapping")
    if not applicant.get("relation_person_name"):
        missing.append("Applicant Relation/Father Name")
    if not applicant.get("address"):
        missing.append("Applicant Address")

    if not purpose:
        missing.append("default Punjab lease Purpose")
    elif not _text(purpose.portal_value):
        missing.append("Punjab Purpose portal mapping")
    return missing


def _validated_components(lease, purpose=None):
    purpose = purpose or default_lease_purpose()
    missing = missing_configuration(lease, purpose)
    if missing:
        raise ValidationError(
            "Punjab e-Stamp configuration is incomplete: " + ", ".join(missing)
        )
    applicant, property_data, portal_data = snapshot_components(lease, purpose)
    return purpose, applicant, property_data, portal_data


def _missing_stored_snapshot(workflow):
    applicant = workflow.applicant_snapshot or {}
    property_data = workflow.property_snapshot or {}
    portal_data = workflow.portal_snapshot or {}
    missing = []
    for key, label in (
        ("name", "Applicant Name"),
        ("cnic", "Applicant CNIC"),
        ("phone", "Applicant Phone"),
        ("relation_name", "Applicant Relation"),
        ("relation_portal_value", "Applicant Relation portal mapping"),
        ("relation_person_name", "Applicant Relation/Father Name"),
        ("address", "Applicant Address"),
    ):
        if not _text(applicant.get(key)):
            missing.append(label)
    for key, label in (
        ("district_name", "District"),
        ("district_portal_value", "District portal mapping"),
        ("tehsil_name", "Tehsil"),
        ("tehsil_portal_value", "Tehsil portal mapping"),
    ):
        if not _text(property_data.get(key)):
            missing.append(label)
    for key, label in (
        ("purpose_name", "Purpose"),
        ("purpose_portal_value", "Purpose portal mapping"),
        ("reason", "Reason"),
    ):
        if not _text(portal_data.get(key)):
            missing.append(label)
    return missing


@transaction.atomic
def prepare_workflow(lease, lease_history, user=None):
    if lease_history.lease_id != lease.pk:
        raise ValidationError("The selected agreement history does not belong to this lease.")
    workflow = (
        LeaseEStampWorkflow.objects.select_for_update()
        .filter(lease_history=lease_history)
        .first()
    )
    if workflow:
        if workflow.lease_id != lease.pk:
            raise ValidationError("The existing Punjab workflow belongs to another lease.")
        if workflow.challan_number or workflow.status != workflow.STATUS_READY:
            return workflow, False

    purpose, _applicant, _property_data, _portal_data = _validated_components(lease)
    if workflow is None:
        workflow, created = LeaseEStampWorkflow.objects.get_or_create(
            lease_history=lease_history,
            defaults={
                "lease": lease,
                "purpose": purpose,
                "created_by": (
                    user if getattr(user, "is_authenticated", False) else None
                ),
                "updated_by": (
                    user if getattr(user, "is_authenticated", False) else None
                ),
            },
        )
    else:
        created = False
    if workflow.lease_id != lease.pk:
        raise ValidationError("The existing Punjab workflow belongs to another lease.")
    if not workflow.challan_number and workflow.status == workflow.STATUS_READY:
        updates = []
        if workflow.purpose_id != purpose.pk:
            workflow.purpose = purpose
            updates.append("purpose")
        if getattr(user, "is_authenticated", False):
            workflow.updated_by = user
            updates.append("updated_by")
        if updates:
            workflow.save(update_fields=[*updates, "updated_at"])
    return workflow, created


@transaction.atomic
def record_challan(workflow, challan_number, psid, user=None, snapshot=None):
    workflow = LeaseEStampWorkflow.objects.select_for_update().select_related(
        "lease__tenant",
        "lease__unit__property__zila",
        "lease__unit__property__tehsil",
        "lease__unit__property__owner_tenant__relation",
        "lease__unit__property__caretaker_tenant__relation",
        "purpose",
    ).get(pk=workflow.pk)
    challan_number = _text(challan_number)
    psid = _text(psid)
    if not challan_number or not psid:
        raise ValidationError("Both Challan number and PSID are required.")
    if workflow.challan_number:
        if workflow.challan_number == challan_number and workflow.psid == psid:
            return workflow, False
        raise ValidationError(
            "This agreement history already has a Challan. Continue the existing Challan first."
        )
    if workflow.applicant_snapshot:
        missing = _missing_stored_snapshot(workflow)
        if missing:
            raise ValidationError(
                "Stored Punjab e-Stamp snapshot is incomplete: " + ", ".join(missing)
            )
        purpose = workflow.purpose
    elif snapshot:
        applicant = snapshot.get("applicant") or {}
        property_data = snapshot.get("property") or {}
        portal_data = snapshot.get("portal") or {}
        workflow.applicant_snapshot = applicant
        workflow.property_snapshot = property_data
        workflow.portal_snapshot = portal_data
        missing = _missing_stored_snapshot(workflow)
        if missing:
            raise ValidationError(
                "Signed Punjab e-Stamp launch snapshot is incomplete: "
                + ", ".join(missing)
            )
        purpose = workflow.purpose
    else:
        purpose, applicant, property_data, portal_data = _validated_components(
            workflow.lease, workflow.purpose
        )
        workflow.applicant_snapshot = applicant
        workflow.property_snapshot = property_data
        workflow.portal_snapshot = portal_data
    workflow.purpose = purpose
    workflow.challan_number = challan_number
    workflow.psid = psid
    workflow.status = workflow.STATUS_CHALLAN_GENERATED
    workflow.challan_generated_at = timezone.now()
    if getattr(user, "is_authenticated", False):
        workflow.updated_by = user
    workflow.save()
    return workflow, True


@transaction.atomic
def mark_paid(workflow, user=None):
    workflow = LeaseEStampWorkflow.objects.select_for_update().get(pk=workflow.pk)
    if not workflow.challan_number or not workflow.psid:
        raise ValidationError("A Challan and PSID are required before marking payment.")
    if workflow.status in {
        workflow.STATUS_PAID,
        workflow.STATUS_STAMP_ISSUED,
        workflow.STATUS_UPLOADED,
    }:
        return workflow, False
    if workflow.status != workflow.STATUS_CHALLAN_GENERATED:
        raise ValidationError("Only a generated Challan can be marked paid.")
    workflow.status = workflow.STATUS_PAID
    workflow.paid_at = timezone.now()
    if getattr(user, "is_authenticated", False):
        workflow.updated_by = user
    workflow.save()
    return workflow, True


@transaction.atomic
def mark_stamp_issued(workflow, stamp_number, user=None):
    workflow = LeaseEStampWorkflow.objects.select_for_update().get(pk=workflow.pk)
    stamp_number = _text(stamp_number)
    if not stamp_number:
        raise ValidationError("Stamp number is required.")
    if workflow.stamp_number:
        if workflow.stamp_number == stamp_number:
            return workflow, False
        raise ValidationError("A different Stamp number is already recorded.")
    if workflow.status not in {workflow.STATUS_PAID, workflow.STATUS_STAMP_ISSUED}:
        raise ValidationError("The Challan must be paid before recording a Stamp.")
    workflow.stamp_number = stamp_number
    workflow.status = workflow.STATUS_STAMP_ISSUED
    workflow.stamp_issued_at = timezone.now()
    if getattr(user, "is_authenticated", False):
        workflow.updated_by = user
    workflow.save()
    return workflow, True


@transaction.atomic
def mark_uploaded(workflow, lease_document, user=None):
    workflow = LeaseEStampWorkflow.objects.select_for_update().get(pk=workflow.pk)
    if lease_document.lease_id != workflow.lease_id:
        raise ValidationError("The E-Stamp document belongs to another lease.")
    if lease_document.lease_history_id not in {None, workflow.lease_history_id}:
        raise ValidationError("The E-Stamp document belongs to another agreement history.")
    if lease_document.category != "estamp_paper":
        raise ValidationError("The workflow document must be an E-Stamp Paper.")
    if workflow.status == workflow.STATUS_UPLOADED and workflow.lease_document_id == lease_document.pk:
        return workflow, False
    if workflow.status != workflow.STATUS_STAMP_ISSUED:
        raise ValidationError("A Stamp must be issued before its PDF can be attached.")
    workflow.lease_document = lease_document
    workflow.status = workflow.STATUS_UPLOADED
    workflow.uploaded_at = timezone.now()
    if getattr(user, "is_authenticated", False):
        workflow.updated_by = user
    workflow.save()
    return workflow, True


@transaction.atomic
def mark_invalid(workflow, error="", user=None):
    workflow = LeaseEStampWorkflow.objects.select_for_update().get(pk=workflow.pk)
    if workflow.status == workflow.STATUS_UPLOADED:
        raise ValidationError("An uploaded E-Stamp cannot be invalidated automatically.")
    workflow.status = workflow.STATUS_INVALID
    workflow.invalidated_at = timezone.now()
    workflow.last_portal_error = _text(error)
    if getattr(user, "is_authenticated", False):
        workflow.updated_by = user
    workflow.save()
    return workflow


@transaction.atomic
def prepare_replacement(workflow, user=None):
    workflow = LeaseEStampWorkflow.objects.select_for_update().get(pk=workflow.pk)
    if workflow.status != workflow.STATUS_INVALID:
        raise ValidationError("Only an invalid Challan can be replaced.")
    history = list(workflow.challan_history or [])
    history.append(
        {
            "challan_number": workflow.challan_number,
            "psid": workflow.psid,
            "stamp_number": workflow.stamp_number,
            "status": workflow.STATUS_INVALID,
            "invalidated_at": (
                workflow.invalidated_at.isoformat()
                if workflow.invalidated_at
                else timezone.now().isoformat()
            ),
            "error": workflow.last_portal_error,
        }
    )
    workflow.challan_history = history
    workflow.replacement_count += 1
    workflow.challan_number = ""
    workflow.psid = ""
    workflow.stamp_number = ""
    workflow.lease_document = None
    workflow.status = workflow.STATUS_READY
    workflow.last_portal_error = ""
    workflow.challan_generated_at = None
    workflow.paid_at = None
    workflow.stamp_issued_at = None
    workflow.uploaded_at = None
    workflow.invalidated_at = None
    if getattr(user, "is_authenticated", False):
        workflow.updated_by = user
    workflow.save()
    return workflow


def payment_message(workflow):
    if not workflow.psid:
        return ""
    property_name = workflow.property_snapshot.get("property_name") or workflow.lease.unit.property.property_name
    unit_number = workflow.property_snapshot.get("unit_number") or workflow.lease.unit.unit_number
    tenant_name = workflow.lease.tenant.get_full_name()
    amount = workflow.portal_snapshot.get("denomination") or ""
    return (
        "Punjab e-Stamp Challan\n"
        f"Property: {property_name}\n"
        f"Unit: {unit_number}\n"
        f"Tenant: {tenant_name}\n"
        f"Challan Number: {workflow.challan_number}\n"
        f"PSID - Copy this number: `{workflow.psid}`\n"
        f"Amount: Rs. {amount}\n"
        "Please pay the PSID above. After payment, open TMS and click\n"
        "Check / Continue."
    )


def payment_whatsapp_url(workflow):
    phone = workflow.applicant_snapshot.get("phone") if workflow.applicant_snapshot else ""
    return build_whatsapp_url(phone, payment_message(workflow)) if phone else ""


def workflow_card_context(lease, lease_history):
    workflow = (
        LeaseEStampWorkflow.objects.filter(
            lease=lease,
            lease_history=lease_history,
        )
        .select_related("purpose", "lease_document")
        .first()
    )
    purpose = workflow.purpose if workflow and workflow.purpose_id else default_lease_purpose()
    current_applicant, current_property, current_portal = snapshot_components(
        lease, purpose
    )
    use_stored_snapshot = bool(workflow and workflow.challan_number)
    applicant = (
        workflow.applicant_snapshot
        if use_stored_snapshot and workflow.applicant_snapshot
        else current_applicant
    )
    property_data = (
        workflow.property_snapshot
        if use_stored_snapshot and workflow.property_snapshot
        else current_property
    )
    portal_data = (
        workflow.portal_snapshot
        if use_stored_snapshot and workflow.portal_snapshot
        else current_portal
    )
    state = workflow.status if workflow else LeaseEStampWorkflow.STATUS_READY
    status_labels = {
        LeaseEStampWorkflow.STATUS_READY: "Not Created",
        LeaseEStampWorkflow.STATUS_CHALLAN_GENERATED: "Not Paid",
        LeaseEStampWorkflow.STATUS_PAID: "Paid",
        LeaseEStampWorkflow.STATUS_STAMP_ISSUED: "Stamp Issued",
        LeaseEStampWorkflow.STATUS_UPLOADED: "Uploaded to TMS",
        LeaseEStampWorkflow.STATUS_INVALID: "Invalid",
        LeaseEStampWorkflow.STATUS_FAILED: "Failed",
    }
    return {
        "workflow": workflow,
        "state": state,
        "status_label": status_labels[state],
        "applicant": applicant,
        "property": property_data,
        "portal": portal_data,
        "missing": (
            _missing_stored_snapshot(workflow)
            if use_stored_snapshot
            else missing_configuration(lease, purpose)
        ),
        "whatsapp_url": payment_whatsapp_url(workflow) if workflow else "",
    }

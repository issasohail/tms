from pathlib import Path
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.dateparse import parse_date
from django.utils import timezone
from tenants.models import Tenant, PendingRegistrationPerson, normalize_cnic
from leases.models import LeaseFamilyMember, LeaseRelationshipType, LeaseVehicle, PendingLeaseVehicleSubmission

PERSON_FIELDS = ("first_name", "last_name", "phone", "date_of_birth", "address")
FILE_FIELDS = ("photo", "cnic_front", "cnic_back")
APPLICANT_FIELDS = (
    "prefix", "first_name", "relation", "last_name", "email", "phone", "phone2",
    "phone3", "cnic", "occupation", "employer_name", "employer_phone",
    "employer_address", "reference_name_1", "reference_phone_1",
    "reference_relation_1", "reference_name_2", "reference_phone_2",
    "reference_relation_2", "nationality", "city", "province", "country",
    "gender", "date_of_birth", "address", "temporary_address",
    "permanent_address", "working_address", "emergency_contact_name",
    "emergency_contact_phone", "emergency_contact_relation",
    "number_of_family_member", "family_member_adults", "family_member_children",
    "nadra_family_no", "notes",
)


def family_member_can_have_blank_cnic(person):
    """Allow a blank CNIC for a family member who is 18 or younger."""
    if person.role != PendingRegistrationPerson.ROLE_FAMILY or normalize_cnic(person.cnic):
        return False
    if not person.date_of_birth:
        return False
    today = timezone.localdate()
    age = today.year - person.date_of_birth.year - (
        (today.month, today.day) < (person.date_of_birth.month, person.date_of_birth.day)
    )
    return age <= 18


def match_tenant_by_cnic(cnic):
    digits = normalize_cnic(cnic)
    return Tenant.objects.filter(cnic_digits=digits).first() if digits else None


def proposed_changes(person):
    tenant = person.matched_tenant
    if not tenant:
        return {}
    changes = {}
    for field in PERSON_FIELDS:
        submitted = getattr(person, field)
        existing = getattr(tenant, field)
        if submitted not in (None, "") and submitted != existing:
            changes[field] = {"existing": existing.isoformat() if hasattr(existing, "isoformat") else existing, "submitted": submitted.isoformat() if hasattr(submitted, "isoformat") else submitted}
    for field in FILE_FIELDS:
        if getattr(person, field):
            changes[field] = {"existing": bool(getattr(tenant, field)), "submitted": True}
    return changes


def applicant_cnic_conflict(submission):
    digits = normalize_cnic((submission.submitted_data or {}).get("cnic"))
    if not digits:
        return None
    return Tenant.objects.exclude(pk=submission.tenant_id).filter(cnic_digits=digits).first()


def _merge_shell_references(shell, target):
    """Move every reverse FK from an unused registration shell, then remove it."""
    target.interested_in.add(*shell.interested_in.all())
    for relation in shell._meta.related_objects:
        if relation.many_to_many:
            continue
        field_name = relation.field.name
        relation.related_model._base_manager.filter(**{field_name: shell}).update(
            **{field_name: target}
        )
    shell.delete()


def apply_registration_applicant(submission, *, collision_action=""):
    """Apply reviewer-selected applicant fields, merging a shell on CNIC conflict."""
    shell = submission.tenant
    conflict = applicant_cnic_conflict(submission)
    if conflict and collision_action != "merge":
        raise ValidationError(
            f"Submitted CNIC already belongs to Tenant #{conflict.pk}. Choose merge or reject."
        )
    tenant = conflict or shell
    decisions = submission.field_decisions or {}
    for field in APPLICANT_FIELDS:
        if decisions.get(field) != "accept_submitted" or field not in submission.submitted_data:
            continue
        value = submission.submitted_data[field]
        if field == "date_of_birth" and value:
            value = parse_date(value)
        setattr(tenant, field, value)
    for field in FILE_FIELDS:
        if decisions.get(field) == "accept_submitted" and getattr(submission, field):
            setattr(tenant, field, getattr(submission, field))
    tenant.is_active = True
    tenant.save()
    if decisions.get("interested_in") == "accept_submitted":
        tenant.interested_in.set(submission.submitted_data.get("interested_in") or [])
    if conflict:
        submission.tenant = tenant
        submission.save(update_fields=["tenant"])
        _merge_shell_references(shell, tenant)
    return tenant, conflict


def _copy_file(source, target, field_name):
    source_field = getattr(source, field_name)
    if not source_field:
        return False
    source_field.open("rb")
    data = source_field.read()
    source_field.close()
    getattr(target, field_name).save(Path(source_field.name).name, ContentFile(data), save=False)
    return True


def resolve_pending_person(person):
    tenant = person.matched_tenant or match_tenant_by_cnic(person.cnic)
    created = False
    if not tenant:
        tenant = Tenant(
            first_name=person.first_name or "Unknown",
            last_name=person.last_name or "Person",
            relation="S/O.",
            cnic=person.cnic,
            phone=person.phone,
            date_of_birth=person.date_of_birth,
            address=person.address,
        )
        for field in FILE_FIELDS:
            _copy_file(person, tenant, field)
        tenant.full_clean(
            exclude=["cnic"] if family_member_can_have_blank_cnic(person) else None
        )
        tenant.save()
        created = True
    else:
        update_fields = []
        for field, decision in (person.field_decisions or {}).items():
            if decision != "accept_submitted":
                continue
            if field in PERSON_FIELDS:
                setattr(tenant, field, getattr(person, field))
                update_fields.append(field)
            elif field in FILE_FIELDS and _copy_file(person, tenant, field):
                update_fields.append(field)
        if update_fields:
            tenant.save(update_fields=list(dict.fromkeys(update_fields + ["updated_at"])))
    person.processed_tenant = tenant
    person.processing_result = {"action": "created" if created else "reused_or_updated", "tenant_id": tenant.pk}
    person.status = PendingRegistrationPerson.STATUS_PROCESSED
    person.save(update_fields=["processed_tenant", "processing_result", "status", "updated_at"])
    return tenant, created


@transaction.atomic
def attach_registration_to_lease(submission, lease, user=None):
    result = {"people": [], "vehicles": 0}
    for person in submission.pending_people.select_for_update().exclude(status=PendingRegistrationPerson.STATUS_REJECTED):
        if person.status == PendingRegistrationPerson.STATUS_PROCESSED and person.processed_tenant_id:
            tenant, created = person.processed_tenant, False
        else:
            tenant, created = resolve_pending_person(person)
        if person.role == PendingRegistrationPerson.ROLE_FAMILY:
            relationship_type = LeaseRelationshipType.objects.filter(pk=person.relationship_type_id).first()
            LeaseFamilyMember.objects.update_or_create(
                lease=lease, primary_tenant=lease.tenant, family_member=tenant,
                defaults={"relationship": (relationship_type.code if relationship_type else person.relationship or "other")[:30], "relationship_type": relationship_type, "lives_with_tenant": True},
            )
        elif person.role == PendingRegistrationPerson.ROLE_PROPOSER:
            lease.proposer = tenant
        elif person.role == PendingRegistrationPerson.ROLE_SECONDER:
            lease.seconder = tenant
        elif person.role == PendingRegistrationPerson.ROLE_WITNESS_1:
            lease.witness1_tenant = tenant
        elif person.role == PendingRegistrationPerson.ROLE_WITNESS_2:
            lease.witness2_tenant = tenant
        result["people"].append({"role": person.role, "tenant_id": tenant.pk, "created": created})
    lease.full_clean()
    lease.save(update_fields=["proposer", "seconder", "witness1_tenant", "witness2_tenant", "updated_at"])
    pending_vehicles = PendingLeaseVehicleSubmission.objects.select_for_update().filter(pending_tenant_submission=submission, status=PendingLeaseVehicleSubmission.STATUS_PENDING)
    for item in pending_vehicles:
        LeaseVehicle.objects.update_or_create(
            lease=lease, registration_number=item.registration_number,
            defaults={"tenant": lease.tenant, "vehicle_type": item.vehicle_type, "make": item.make, "model": item.model, "color": item.color, "year": item.year, "owner_name": item.owner_name, "owner_cnic": item.owner_cnic, "registration_book_photo": item.registration_book_photo, "vehicle_photo": item.vehicle_photo, "is_active": True},
        )
        item.lease = lease; item.tenant = lease.tenant; item.status = PendingLeaseVehicleSubmission.STATUS_APPROVED; item.reviewed_by = user; item.reviewed_at = timezone.now(); item.save(update_fields=["lease", "tenant", "status", "reviewed_by", "reviewed_at"])
        result["vehicles"] += 1
    return result

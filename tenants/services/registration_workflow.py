from pathlib import Path
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from tenants.models import Tenant, PendingRegistrationPerson, normalize_cnic
from leases.models import LeaseFamilyMember, LeaseRelationshipType, LeaseVehicle, PendingLeaseVehicleSubmission

PERSON_FIELDS = ("first_name", "last_name", "phone", "date_of_birth", "address")
FILE_FIELDS = ("photo", "cnic_front", "cnic_back")


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
        tenant.full_clean()
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
            defaults={"tenant": lease.tenant, "vehicle_type": item.vehicle_type, "make": item.make, "model": item.model, "color": item.color, "year": item.year, "owner_name": item.owner_name, "owner_cnic": item.owner_cnic, "parking_slot": item.parking_slot, "registration_book_photo": item.registration_book_photo, "vehicle_photo": item.vehicle_photo, "is_active": True},
        )
        item.lease = lease; item.tenant = lease.tenant; item.status = PendingLeaseVehicleSubmission.STATUS_APPROVED; item.reviewed_by = user; item.reviewed_at = timezone.now(); item.save(update_fields=["lease", "tenant", "status", "reviewed_by", "reviewed_at"])
        result["vehicles"] += 1
    return result

from pathlib import Path
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.dateparse import parse_date
from django.utils import timezone
from tenants.models import Tenant, PendingRegistrationPerson, normalize_cnic
from leases.models import LeaseFamilyMember, LeaseRelationshipType, LeaseVehicle, PendingLeaseVehicleSubmission
from core.utils.identity import validate_cnic

PERSON_FIELDS = ("first_name", "last_name", "phone", "date_of_birth", "address")
FILE_FIELDS = ("photo", "cnic_front", "cnic_back")
REQUIRED_PARTY_ROLES = (
    (PendingRegistrationPerson.ROLE_PROPOSER, "Proposer"),
    (PendingRegistrationPerson.ROLE_SECONDER, "Seconder"),
)
APPLICANT_FIELDS = (
    "prefix", "first_name", "relation", "last_name", "email", "phone", "phone2",
    "phone3", "cnic", "occupation", "employer_name", "employer_phone",
    "employer_address", "reference_name_1", "reference_phone_1",
    "reference_relation_1", "reference_name_2", "reference_phone_2",
    "reference_relation_2", "nationality", "city", "province", "country",
    "gender", "date_of_birth", "cnic_issue_date", "cnic_expiry_date",
    "address", "temporary_address", "permanent_address",
    "temporary_address_urdu", "permanent_address_urdu",
    "working_address", "emergency_contact_name",
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


def registration_required_party_reviews(submission):
    all_people = list(submission.pending_people.all())
    people = {
        person.role: person
        for person in all_people
        if person.role in dict(REQUIRED_PARTY_ROLES)
    }
    applicant = applicant_cnic_conflict(submission) or submission.tenant
    applicant_digits = normalize_cnic(
        (submission.submitted_data or {}).get("cnic") or applicant.cnic
    )
    family_people = [
        person
        for person in all_people
        if person.role == PendingRegistrationPerson.ROLE_FAMILY
        and person.status != PendingRegistrationPerson.STATUS_REJECTED
    ]
    family_tenant_ids = {
        tenant_id
        for person in family_people
        for tenant_id in (person.matched_tenant_id, person.processed_tenant_id)
        if tenant_id
    }
    family_cnic_digits = {
        digits
        for person in family_people
        if (digits := normalize_cnic(person.cnic))
    }
    current_lease = applicant.current_lease
    if current_lease:
        current_family = current_lease.family_members.select_related(
            "family_member"
        )
        family_tenant_ids.update(
            current_family.values_list("family_member_id", flat=True)
        )
        family_cnic_digits.update(
            digits
            for value in current_family.values_list("family_member__cnic", flat=True)
            if (digits := normalize_cnic(value))
        )

    party_tenant_ids = {
        role: (
            person.matched_tenant_id or person.processed_tenant_id
            if person
            else None
        )
        for role, person in people.items()
    }
    party_cnic_digits = {
        role: normalize_cnic(person.cnic) if person else ""
        for role, person in people.items()
    }
    reviews = []
    for role, label in REQUIRED_PARTY_ROLES:
        person = people.get(role)
        missing = []
        if not person:
            missing.append("record")
        else:
            for field_name, field_label in (
                ("first_name", "first name"),
                ("last_name", "last name"),
                ("cnic", "CNIC"),
                ("phone", "phone"),
            ):
                if not str(getattr(person, field_name, "") or "").strip():
                    missing.append(field_label)
            if person.cnic:
                try:
                    validate_cnic(person.cnic)
                except ValidationError:
                    if "CNIC" not in missing:
                        missing.append("valid CNIC")
            person_digits = normalize_cnic(person.cnic)
            person_tenant_id = (
                person.matched_tenant_id or person.processed_tenant_id
            )
            if (
                person_tenant_id == applicant.pk
                or (person_digits and person_digits == applicant_digits)
            ):
                missing.append("must be someone other than the tenant")
            if (
                person_tenant_id in family_tenant_ids
                or (person_digits and person_digits in family_cnic_digits)
            ):
                missing.append("must not be a family member")
            other_role = (
                PendingRegistrationPerson.ROLE_SECONDER
                if role == PendingRegistrationPerson.ROLE_PROPOSER
                else PendingRegistrationPerson.ROLE_PROPOSER
            )
            if (
                person_digits
                and person_digits == party_cnic_digits.get(other_role)
            ) or (
                person_tenant_id
                and person_tenant_id == party_tenant_ids.get(other_role)
            ):
                missing.append("must be different from the other required party")
        reviews.append(
            {
                "role": role,
                "label": label,
                "person": person,
                "missing": missing,
                "is_complete": not missing,
            }
        )
    return reviews


def registration_required_party_errors(submission):
    return [
        f"{review['label']}: {', '.join(review['missing'])}"
        for review in registration_required_party_reviews(submission)
        if review["missing"]
    ]


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


def _stored_file_exists(field_file):
    try:
        return bool(field_file and field_file.name and field_file.storage.exists(field_file.name))
    except (OSError, ValueError):
        return False


def _record_missing_file(missing_files, context, field_name, path):
    if missing_files is not None:
        missing_files.append(
            f"{context}: {field_name.replace('_', ' ').title()} file is missing. "
            f"The saved path was kept unchanged: {path}"
        )


def _apply_deferred_file_paths(tenant, deferred_file_paths):
    if not deferred_file_paths:
        return
    Tenant.objects.filter(pk=tenant.pk).update(**deferred_file_paths)
    for field_name, path in deferred_file_paths.items():
        setattr(tenant, field_name, path)


def apply_registration_applicant(submission, *, collision_action="", missing_files=None):
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
    deferred_missing_files = {}
    for field in FILE_FIELDS:
        source_file = getattr(submission, field)
        if decisions.get(field) != "accept_submitted" or not source_file:
            continue
        if _stored_file_exists(source_file):
            setattr(tenant, field, source_file)
        else:
            deferred_missing_files[field] = source_file.name
            _record_missing_file(
                missing_files, "Applicant", field, source_file.name
            )
    tenant.is_active = True
    tenant.save()
    # Bypass image-cropping's pre-save file read for paths whose files are absent.
    _apply_deferred_file_paths(tenant, deferred_missing_files)
    if decisions.get("interested_in") == "accept_submitted":
        tenant.interested_in.set(submission.submitted_data.get("interested_in") or [])
    if conflict:
        submission.tenant = tenant
        submission.save(update_fields=["tenant"])
        _merge_shell_references(shell, tenant)
    return tenant, conflict


def _copy_file(
    source,
    target,
    field_name,
    *,
    deferred_file_paths=None,
    missing_files=None,
    context="Registration person",
):
    source_field = getattr(source, field_name)
    if not source_field:
        return False
    if not _stored_file_exists(source_field):
        if deferred_file_paths is not None:
            deferred_file_paths[field_name] = source_field.name
        _record_missing_file(missing_files, context, field_name, source_field.name)
        return True
    opened = False
    try:
        source_field.open("rb")
        opened = True
        data = source_field.read()
    except (FileNotFoundError, OSError):
        if deferred_file_paths is not None:
            deferred_file_paths[field_name] = source_field.name
        _record_missing_file(missing_files, context, field_name, source_field.name)
        return True
    finally:
        if opened:
            source_field.close()
    getattr(target, field_name).save(Path(source_field.name).name, ContentFile(data), save=False)
    return True


def resolve_pending_person(person, missing_files=None):
    tenant = person.matched_tenant or match_tenant_by_cnic(person.cnic)
    created = False
    deferred_file_paths = {}
    person_context = f"{person.get_role_display()} ({person.first_name} {person.last_name})".strip()
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
            _copy_file(
                person,
                tenant,
                field,
                deferred_file_paths=deferred_file_paths,
                missing_files=missing_files,
                context=person_context,
            )
        tenant.full_clean(
            exclude=["cnic"] if family_member_can_have_blank_cnic(person) else None
        )
        tenant.save()
        _apply_deferred_file_paths(tenant, deferred_file_paths)
        created = True
    else:
        update_fields = []
        for field, decision in (person.field_decisions or {}).items():
            if decision != "accept_submitted":
                continue
            if field in PERSON_FIELDS:
                setattr(tenant, field, getattr(person, field))
                update_fields.append(field)
            elif field in FILE_FIELDS and _copy_file(
                person,
                tenant,
                field,
                deferred_file_paths=deferred_file_paths,
                missing_files=missing_files,
                context=person_context,
            ):
                update_fields.append(field)
        if update_fields:
            tenant.save(update_fields=list(dict.fromkeys(update_fields + ["updated_at"])))
            _apply_deferred_file_paths(tenant, deferred_file_paths)
    person.processed_tenant = tenant
    person.processing_result = {"action": "created" if created else "reused_or_updated", "tenant_id": tenant.pk}
    person.status = PendingRegistrationPerson.STATUS_PROCESSED
    person.save(update_fields=["processed_tenant", "processing_result", "status", "updated_at"])
    return tenant, created


def _resolve_registration_people(submission, missing_files=None):
    resolved = []
    for person in submission.pending_people.select_for_update().exclude(
        status=PendingRegistrationPerson.STATUS_REJECTED
    ):
        if person.status == PendingRegistrationPerson.STATUS_PROCESSED and person.processed_tenant_id:
            tenant, created = person.processed_tenant, False
        else:
            tenant, created = resolve_pending_person(person, missing_files=missing_files)
        resolved.append((person, tenant, created))
    return resolved


@transaction.atomic
def process_registration_people(submission, missing_files=None):
    return [
        {"role": person.role, "tenant_id": tenant.pk, "created": created}
        for person, tenant, created in _resolve_registration_people(
            submission, missing_files=missing_files
        )
    ]


@transaction.atomic
def attach_registration_to_lease(submission, lease, user=None, missing_files=None):
    result = {"people": [], "vehicles": 0}
    witness_update_fields = set()
    for person, tenant, created in _resolve_registration_people(
        submission, missing_files=missing_files
    ):
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
            witness_update_fields.add("witness1_tenant")
        elif person.role == PendingRegistrationPerson.ROLE_WITNESS_2:
            lease.witness2_tenant = tenant
            witness_update_fields.add("witness2_tenant")
        result["people"].append({"role": person.role, "tenant_id": tenant.pk, "created": created})
    lease.full_clean()
    lease.save(update_fields=["proposer", "seconder", "witness1_tenant", "witness2_tenant", "updated_at"])
    if witness_update_fields:
        today = timezone.localdate()
        current_history = (
            lease.renewals.select_for_update()
            .filter(start_date__lte=today, end_date__gte=today)
            .order_by("-renewal_number", "-id")
            .first()
            or lease.renewals.select_for_update()
            .order_by("-renewal_number", "-id")
            .first()
        )
        if current_history:
            for field_name in witness_update_fields:
                setattr(current_history, field_name, getattr(lease, field_name))
            history_update_fields = list(witness_update_fields)
            if getattr(user, "is_authenticated", False):
                current_history.updated_by = user
                history_update_fields.append("updated_by")
            current_history.save(update_fields=history_update_fields + ["updated_at"])
            result["lease_history_id"] = current_history.pk
    pending_vehicles = PendingLeaseVehicleSubmission.objects.select_for_update().filter(pending_tenant_submission=submission, status=PendingLeaseVehicleSubmission.STATUS_PENDING)
    for item in pending_vehicles:
        vehicle_files = {}
        vehicle_context = f"Vehicle {item.registration_number}"
        for field_name in ("registration_book_photo", "vehicle_photo"):
            source_file = getattr(item, field_name)
            vehicle_files[field_name] = source_file.name if source_file else None
            if source_file and not _stored_file_exists(source_file):
                _record_missing_file(
                    missing_files, vehicle_context, field_name, source_file.name
                )
        LeaseVehicle.objects.update_or_create(
            lease=lease, registration_number=item.registration_number,
            defaults={"tenant": lease.tenant, "vehicle_type": item.vehicle_type, "make": item.make, "model": item.model, "color": item.color, "year": item.year, "owner_name": item.owner_name, "owner_cnic": item.owner_cnic, **vehicle_files, "is_active": True},
        )
        item.lease = lease; item.tenant = lease.tenant; item.status = PendingLeaseVehicleSubmission.STATUS_APPROVED; item.reviewed_by = user; item.reviewed_at = timezone.now(); item.save(update_fields=["lease", "tenant", "status", "reviewed_by", "reviewed_at"])
        result["vehicles"] += 1
    return result

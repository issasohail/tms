import os

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils import timezone

from leases.models import LeaseVehicle, LeaseVehicleType, PendingLeaseVehicleSubmission


VEHICLE_FIELD_NAMES = [
    "make",
    "model",
    "color",
    "year",
    "owner_name",
    "owner_cnic",
]


def create_pending_vehicle_submissions_from_post(
    request,
    *,
    lease=None,
    tenant=None,
    pending_tenant_submission=None,
    source="",
):
    created = []
    errors = []
    total_raw = request.POST.get("vehicle-TOTAL_FORMS") or "0"
    try:
        total = max(0, min(int(total_raw), 5))
    except (TypeError, ValueError):
        total = 0

    active_types = {
        str(item.pk): item
        for item in LeaseVehicleType.objects.filter(is_active=True)
    }

    for index in range(total):
        prefix = f"vehicle-{index}"
        vehicle_type_id = (request.POST.get(f"{prefix}-vehicle_type") or "").strip()
        registration_number = (
            request.POST.get(f"{prefix}-registration_number") or ""
        ).strip()
        values = {
            field: (request.POST.get(f"{prefix}-{field}") or "").strip()
            for field in VEHICLE_FIELD_NAMES
        }
        vehicle_photo = request.FILES.get(f"{prefix}-vehicle_photo")
        registration_book_photo = request.FILES.get(
            f"{prefix}-registration_book_photo"
        )

        has_any_data = any(
            [
                vehicle_type_id,
                registration_number,
                *values.values(),
                vehicle_photo,
                registration_book_photo,
            ]
        )
        if not has_any_data:
            continue

        if not vehicle_type_id or vehicle_type_id not in active_types:
            errors.append(f"Vehicle {index + 1}: vehicle type is required.")
            continue
        if not registration_number:
            errors.append(f"Vehicle {index + 1}: registration number is required.")
            continue

        duplicate_pending = PendingLeaseVehicleSubmission.objects.filter(
            registration_number__iexact=registration_number,
            status=PendingLeaseVehicleSubmission.STATUS_PENDING,
        )
        if pending_tenant_submission is not None:
            duplicate_pending = duplicate_pending.filter(
                pending_tenant_submission=pending_tenant_submission
            )
        elif lease is not None:
            duplicate_pending = duplicate_pending.filter(lease=lease)
        elif tenant is not None:
            duplicate_pending = duplicate_pending.filter(tenant=tenant)
        if duplicate_pending.exists() or any(
            item.registration_number.lower() == registration_number.lower()
            for item in created
        ):
            errors.append(
                f"Vehicle {index + 1}: registration number {registration_number} is already in this submission."
            )
            continue

        existing_vehicle = LeaseVehicle.objects.filter(
            registration_number__iexact=registration_number,
            is_active=True,
        ).select_related("lease", "tenant").first()
        if existing_vehicle:
            if lease is not None and existing_vehicle.lease_id == lease.pk:
                message = "already exists on this lease"
            elif existing_vehicle.lease.status == "active":
                message = f"is already on active Lease #{existing_vehicle.lease_id}"
            elif tenant is not None and existing_vehicle.tenant_id == tenant.pk:
                message = "is already registered for this tenant"
            else:
                message = "already exists as an active vehicle record"
            errors.append(f"Vehicle {index + 1}: registration number {registration_number} {message}.")
            continue

        year = None
        if values["year"]:
            try:
                year = int(values["year"])
            except (TypeError, ValueError):
                errors.append(f"Vehicle {index + 1}: year must be a number.")
                continue

        submission = PendingLeaseVehicleSubmission(
            lease=lease,
            tenant=tenant,
            pending_tenant_submission=pending_tenant_submission,
            source=source,
            vehicle_type=active_types[vehicle_type_id],
            registration_number=registration_number,
            make=values["make"],
            model=values["model"],
            color=values["color"],
            year=year,
            owner_name=values["owner_name"],
            owner_cnic=values["owner_cnic"],
            vehicle_photo=vehicle_photo,
            registration_book_photo=registration_book_photo,
        )
        try:
            submission.full_clean()
        except ValidationError as exc:
            errors.extend(exc.messages)
            continue
        submission.save()
        created.append(submission)

    return created, errors


def copy_pending_vehicle_to_lease_vehicle(submission, *, lease=None, reviewed_by=None):
    lease = lease or submission.lease
    if not lease:
        raise ValidationError("Assign/create a lease before approving this vehicle.")

    vehicle = LeaseVehicle(
        lease=lease,
        tenant=submission.tenant or lease.tenant,
        vehicle_type=submission.vehicle_type,
        registration_number=submission.registration_number,
        make=submission.make,
        model=submission.model,
        color=submission.color,
        year=submission.year,
        owner_name=submission.owner_name,
        owner_cnic=submission.owner_cnic,
    )

    if submission.registration_book_photo:
        submission.registration_book_photo.open("rb")
        vehicle.registration_book_photo.save(
            os.path.basename(submission.registration_book_photo.name),
            ContentFile(submission.registration_book_photo.read()),
            save=False,
        )
    if submission.vehicle_photo:
        submission.vehicle_photo.open("rb")
        vehicle.vehicle_photo.save(
            os.path.basename(submission.vehicle_photo.name),
            ContentFile(submission.vehicle_photo.read()),
            save=False,
        )
    vehicle.save()
    submission.lease = lease
    submission.tenant = submission.tenant or lease.tenant
    submission.status = PendingLeaseVehicleSubmission.STATUS_APPROVED
    submission.reviewed_by = reviewed_by
    submission.reviewed_at = timezone.now()
    submission.save(
        update_fields=[
            "lease",
            "tenant",
            "status",
            "reviewed_by",
            "reviewed_at",
        ]
    )
    if hasattr(lease, "has_vehicle") and lease.has_vehicle is not True:
        lease.has_vehicle = True
        lease.save(update_fields=["has_vehicle"])
    return vehicle


def attach_pending_vehicle_submissions_to_lease(
    *,
    lease,
    tenant=None,
    pending_tenant_submission=None,
):
    queryset = PendingLeaseVehicleSubmission.objects.filter(
        lease__isnull=True,
        status=PendingLeaseVehicleSubmission.STATUS_PENDING,
    )
    if pending_tenant_submission is not None:
        queryset = queryset.filter(pending_tenant_submission=pending_tenant_submission)
    elif tenant is not None:
        queryset = queryset.filter(tenant=tenant)
    else:
        return 0
    return queryset.update(lease=lease, tenant=tenant or lease.tenant)

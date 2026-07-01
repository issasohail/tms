from django.db import transaction
from django.utils import timezone

from .models import HandymanProfile, MaintenanceHandymanAssignment


@transaction.atomic
def assign_handyman(maintenance_request, handyman, *, assigned_by=None, notes="", status=None):
    MaintenanceHandymanAssignment.objects.select_for_update().filter(
        maintenance_request=maintenance_request,
        is_current=True,
    ).update(is_current=False, updated_at=timezone.now())
    return MaintenanceHandymanAssignment.objects.create(
        maintenance_request=maintenance_request,
        handyman=handyman,
        assigned_by=assigned_by,
        notes=notes or "",
        status=status or MaintenanceHandymanAssignment.STATUS_ASSIGNED,
        is_current=True,
    )


def current_assignment_for(maintenance_request):
    return (
        MaintenanceHandymanAssignment.objects.select_related("handyman")
        .filter(maintenance_request=maintenance_request, is_current=True)
        .first()
    )


def active_assignment_for_handyman_phone(phone_number):
    digits = _digits(phone_number)
    if not digits:
        return None
    handymen = HandymanProfile.objects.filter(is_active=True)
    handyman = None
    for item in handymen:
        if _phone_matches(digits, item.whatsapp_number or item.phone):
            handyman = item
            break
    if not handyman:
        return None
    return (
        MaintenanceHandymanAssignment.objects.select_related("maintenance_request", "handyman")
        .filter(
            handyman=handyman,
            is_current=True,
            status__in=[
                MaintenanceHandymanAssignment.STATUS_ASSIGNED,
                MaintenanceHandymanAssignment.STATUS_ACCEPTED,
                MaintenanceHandymanAssignment.STATUS_IN_PROGRESS,
            ],
        )
        .order_by("-assigned_at", "-id")
        .first()
    )


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _phone_matches(target_digits, candidate):
    candidate_digits = _digits(candidate)
    return bool(candidate_digits and (candidate_digits.endswith(target_digits[-10:]) or target_digits.endswith(candidate_digits[-10:])))

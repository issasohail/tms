from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from leases.models import AgreementSignatureTemplate, LeaseDocument


ESTAMP_CATEGORY = "estamp_paper"
OVERRIDE_PERMISSION = "leases.override_estamp_age"
PAPER_SIZES = {
    "legal": (612.0, 1008.0),
    "letter": (612.0, 792.0),
}


@dataclass(frozen=True)
class EStampStatus:
    document: LeaseDocument | None
    age_days: int | None
    max_age_days: int
    is_over_age: bool
    can_override: bool


def latest_estamp(lease):
    return (
        lease.documents.filter(category=ESTAMP_CATEGORY, is_active=True)
        .order_by("-uploaded_at", "-pk")
        .first()
    )


def estamp_status(lease, user=None, *, today=None, config=None):
    document = latest_estamp(lease)
    config = config or AgreementSignatureTemplate.current()
    max_age_days = max(0, int(getattr(config, "estamp_max_age_days", 30) or 0))
    age_days = None
    if document:
        uploaded_date = timezone.localtime(document.uploaded_at).date()
        age_days = max(0, ((today or timezone.localdate()) - uploaded_date).days)
    is_over_age = bool(document and max_age_days and age_days > max_age_days)
    can_override = bool(
        user
        and getattr(user, "is_authenticated", False)
        and (getattr(user, "is_superuser", False) or user.has_perm(OVERRIDE_PERMISSION))
    )
    return EStampStatus(document, age_days, max_age_days, is_over_age, can_override)


def authorize_estamp(lease, user, *, allow_over_age=False):
    status = estamp_status(lease, user)
    if status.document is None:
        raise ValidationError("No E-Stamp Paper has been uploaded for this lease.")
    if status.is_over_age and not (allow_over_age and status.can_override):
        raise PermissionDenied(
            "This E-Stamp Paper is older than the configured maximum age."
        )
    return status.document

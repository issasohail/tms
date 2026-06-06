from django.core import signing
from django.shortcuts import get_object_or_404

from leases.models import Lease


PUBLIC_MAINTENANCE_SALT = "public-maintenance-request-link"
PUBLIC_CONFIRMATION_SALT = "public-maintenance-confirmation"


def make_public_maintenance_token(lease):
    return signing.TimestampSigner(salt=PUBLIC_MAINTENANCE_SALT).sign(str(lease.pk))


def get_public_maintenance_lease(token):
    lease_id = signing.TimestampSigner(salt=PUBLIC_MAINTENANCE_SALT).unsign(token)
    return get_object_or_404(
        Lease.objects.select_related("tenant", "unit", "unit__property"),
        pk=lease_id,
        status="active",
    )


def make_confirmation_token(request_obj):
    return signing.TimestampSigner(salt=PUBLIC_CONFIRMATION_SALT).sign(str(request_obj.pk))


def get_confirmation_request_id(token):
    return signing.TimestampSigner(salt=PUBLIC_CONFIRMATION_SALT).unsign(token)

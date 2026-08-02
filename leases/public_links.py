from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from core.public_urls import build_public_url
from whatsapp.models import WhatsAppExternalLinkToken


def create_public_ledger_link(lease, phone_number="", staff_user=None, hours=24):
    return WhatsAppExternalLinkToken.objects.create(
        link_type=WhatsAppExternalLinkToken.LINK_LEDGER_VIEW,
        phone_number=phone_number or getattr(lease.tenant, "phone", "") or "",
        tenant=lease.tenant,
        staff_user=staff_user,
        target_app_label="leases",
        target_model="Lease",
        target_object_id=lease.pk,
        metadata={"lease_id": lease.pk},
        expires_at=timezone.now() + timedelta(hours=hours),
    )


def public_ledger_path(link):
    return reverse("leases:public_lease_ledger", args=[link.token])


def public_ledger_url(link):
    return build_public_url("leases:public_lease_ledger", args=[link.token])

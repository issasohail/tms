from django.conf import settings
from django.core.cache import cache

from .currency import currency_symbol
from .models import GlobalSettings


def global_settings(request):
    settings_obj = cache.get("core.global_settings")

    if settings_obj is None:
        settings_obj = GlobalSettings.get_solo()
        cache.set("core.global_settings", settings_obj, 60)

    pending_approval_count = 0
    if getattr(request.user, "is_authenticated", False):
        pending_approval_count = cache.get("core.pending_approval_count")
        if pending_approval_count is None:
            from leases.models import (
                Lease,
                PendingAgreementApproval,
                PendingLeaseFamilyMemberSubmission,
                PendingPoliceVerificationSubmission,
            )
            from tenants.models import TenantRegistrationSubmission
            from whatsapp.models import (
                PendingWhatsAppMaintenance,
                PendingWhatsAppMedia,
                PendingWhatsAppPayment,
            )

            pending_approval_count = sum(
                (
                    Lease.objects.filter(status="pending_approval").count(),
                    PendingAgreementApproval.objects.filter(status="pending").count(),
                    PendingLeaseFamilyMemberSubmission.objects.filter(status="pending").count(),
                    PendingPoliceVerificationSubmission.objects.filter(status="pending").count(),
                    TenantRegistrationSubmission.objects.filter(
                        status__in=TenantRegistrationSubmission.EDITABLE_STATUSES
                    ).count(),
                    PendingWhatsAppPayment.objects.filter(
                        status__in=["pending", "confirmed"],
                        approved=False,
                        rejected=False,
                    ).count(),
                    PendingWhatsAppMaintenance.objects.filter(status="pending").count(),
                    PendingWhatsAppMedia.objects.filter(status="pending")
                    .exclude(purpose__in=["payment", "maintenance"])
                    .exclude(maintenance_submissions__status="pending")
                    .exclude(police_verification_submissions__status="pending")
                    .distinct()
                    .count(),
                )
            )
            cache.set("core.pending_approval_count", pending_approval_count, 30)

    return {
        "GLOBAL_SETTINGS": settings_obj,
        "CURRENCY_SYMBOL": currency_symbol(settings_obj),
        "PENDING_APPROVAL_COUNT": pending_approval_count,
        # Environment variables available in every template
        "APP_ENVIRONMENT": getattr(
            settings,
            "APP_ENVIRONMENT",
            "production",
        ),
        "APP_ENVIRONMENT_LABEL": getattr(
            settings,
            "APP_ENVIRONMENT_LABEL",
            "PRODUCTION",
        ),
    }

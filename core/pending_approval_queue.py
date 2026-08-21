from django.db.models import Count, Q


def pending_approval_status_filters():
    """Return the pending rules shared by the navbar count and approval page."""
    from tenants.models import TenantRegistrationSubmission
    from whatsapp.models import PendingWhatsAppPayment

    return {
        "common": Q(status="pending"),
        "lease": Q(status="pending_approval"),
        "payment": Q(
            status__in=[
                PendingWhatsAppPayment.STATUS_PENDING,
                PendingWhatsAppPayment.STATUS_CONFIRMED,
            ],
            approved=False,
            rejected=False,
        ),
        "registration": Q(
            status__in=TenantRegistrationSubmission.EDITABLE_STATUSES
        ),
    }


def eligible_pending_media_queryset():
    from whatsapp.models import PendingWhatsAppMaintenance, PendingWhatsAppMedia

    return (
        PendingWhatsAppMedia.objects.exclude(
            purpose__in=[
                PendingWhatsAppMedia.PURPOSE_PAYMENT,
                PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
            ]
        )
        .exclude(
            maintenance_submissions__status=PendingWhatsAppMaintenance.STATUS_PENDING
        )
        .exclude(police_verification_submissions__status="pending")
    )


def actionable_media_count(queryset):
    """
    Count approval actions, not files: one uploaded media batch is one action,
    while every standalone media record is one action.
    """
    counts = queryset.aggregate(
        standalone_count=Count(
            "pk",
            filter=Q(batch_key__isnull=True),
        ),
        batch_count=Count(
            "batch_key",
            filter=Q(batch_key__isnull=False),
            distinct=True,
        ),
    )
    return counts["standalone_count"] + counts["batch_count"]


def pending_approval_actionable_counts(request=None):
    request_cache_attr = "_tms_pending_approval_counts"
    if request is not None and hasattr(request, request_cache_attr):
        return getattr(request, request_cache_attr)

    from leases.models import (
        Lease,
        PendingAgreementApproval,
        PendingLeaseFamilyMemberSubmission,
        PendingPoliceVerificationSubmission,
    )
    from tenants.models import TenantRegistrationSubmission
    from whatsapp.models import PendingWhatsAppMaintenance, PendingWhatsAppPayment

    pending = pending_approval_status_filters()
    pending_media = eligible_pending_media_queryset().filter(pending["common"])

    counts = {
        "lease": Lease.objects.filter(pending["lease"]).count(),
        "agreement": PendingAgreementApproval.objects.filter(
            pending["common"]
        ).count(),
        "payment": PendingWhatsAppPayment.objects.filter(
            pending["payment"]
        ).count(),
        "media": actionable_media_count(pending_media),
        "maintenance": PendingWhatsAppMaintenance.objects.filter(
            pending["common"]
        ).count(),
        "family": PendingLeaseFamilyMemberSubmission.objects.filter(
            pending["common"]
        ).count(),
        "police": PendingPoliceVerificationSubmission.objects.filter(
            pending["common"]
        ).count(),
        "registration": TenantRegistrationSubmission.objects.filter(
            pending["registration"]
        ).count(),
    }
    if request is not None:
        setattr(request, request_cache_attr, counts)
        setattr(request, "_tms_pending_approval_count", sum(counts.values()))
    return counts


def pending_approval_count(request=None):
    request_cache_attr = "_tms_pending_approval_count"
    if request is not None and hasattr(request, request_cache_attr):
        return getattr(request, request_cache_attr)

    # Approval/rejection actions are expected to update the badge immediately.
    # Keep cross-request results exact; request-local memoization above still
    # prevents duplicate work within a single response.
    total = sum(pending_approval_actionable_counts(request).values())
    if request is not None:
        setattr(request, request_cache_attr, total)
    return total

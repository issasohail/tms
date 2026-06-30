from django.utils import timezone

from whatsapp.models import TrustedDeviceRegistry


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def record_external_link_access(request, link, user_type=None):
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    fingerprint = request.GET.get("fp") or request.POST.get("fp") or ""
    resolved_user_type = user_type or TrustedDeviceRegistry.USER_TYPE_GUEST
    phone_number = getattr(link, "phone_number", "") or ""
    tenant = getattr(link, "tenant", None)
    staff_user = getattr(link, "staff_user", None)

    device = TrustedDeviceRegistry.objects.create(
        user_type=resolved_user_type,
        tenant=tenant,
        staff_user=staff_user,
        phone_number=phone_number,
        browser_fingerprint=fingerprint or None,
        ip_address=client_ip(request),
        user_agent=user_agent,
        trusted_status=TrustedDeviceRegistry.TRUSTED_PENDING,
        notes=f"External link opened: {getattr(link, 'link_type', '')}",
    )
    if getattr(link, "used_at", None) is None:
        link.used_at = timezone.now()
        link.save(update_fields=["used_at"])
    return device

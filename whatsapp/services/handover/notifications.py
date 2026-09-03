from whatsapp.models import WhatsAppConversation, WhatsAppHandover, WhatsAppStaffActionLog
from whatsapp.services.handover.routing import eligible_staff
from whatsapp.services.whatsapp import WhatsAppService
from core.utils.identity import format_phone


def notify_new_handover(handover, service=None):
    service = service or WhatsAppService()
    staff = eligible_staff(handover)
    from core.models import GlobalSettings
    if not GlobalSettings.get_solo().whatsapp_handover_notify_multiple_staff:
        staff = staff[:1]
    sent = []
    for user in staff:
        number = getattr(user, "whatsapp_number", "")
        if not number:
            continue
        service.send_text(number, staff_notification_text(handover), tenant=handover.tenant, lease=handover.lease)
        remember_latest_handover(user, number, handover)
        sent.append(user.pk)
    if sent and handover.status == WhatsAppHandover.STATUS_NEW:
        handover.status = WhatsAppHandover.STATUS_NOTIFIED
        handover.save(update_fields=["status", "updated_at"])
    if not sent:
        WhatsAppStaffActionLog.objects.create(
            phone_number=handover.tenant_phone,
            action="handover_notification_unrouted",
            status=WhatsAppStaffActionLog.ACTION_STATUS_BLOCKED,
            property=handover.property,
            tenant=handover.tenant,
            lease=handover.lease,
            details={"handover_reference": handover.reference, "department": handover.department},
        )
    return sent


def notify_tenant_message(handover, text, service=None):
    service = service or WhatsAppService()
    recipients = [handover.assigned_staff] if handover.assigned_staff_id else eligible_staff(handover)
    for user in recipients:
        number = getattr(user, "whatsapp_number", "") if user else ""
        if number:
            service.send_text(
                number,
                staff_notification_text(handover, message=text, heading="Received another message from"),
                tenant=handover.tenant,
                lease=handover.lease,
            )
            remember_latest_handover(user, number, handover)


def staff_notification_text(handover, message=None, heading="Received message from"):
    property_unit = f"{handover.property or '-'} / {handover.unit or '-'}"
    tenant_message = (message if message is not None else handover.tenant_message or "").strip()
    if not tenant_message:
        tenant_message = "Media attached below."
    return (
        f"{heading}\n\n"
        f"Tenant: {tenant_name(handover)}\n"
        f"Phone: {format_phone(handover.tenant_phone)}\n"
        f"Property / Unit: {property_unit}\n"
        f"Reference: {handover.reference}\n\n"
        f"Message:\n{tenant_message[:1200]}\n\n"
        "To respond, type:\nReply: your message\n\n"
        f"If replying to an older message, use:\nReply {handover.reference}: your message"
    )


def remember_latest_handover(user, phone_number, handover):
    normalized = WhatsAppConversation._meta.get_field("phone_number").to_python(phone_number)
    if not normalized:
        return
    conversation, _created = WhatsAppConversation.objects.get_or_create(phone_number=normalized)
    context = dict(conversation.context or {})
    context["latest_notified_handover_id"] = handover.pk
    conversation.context = context
    update_fields = ["context", "updated_at"]
    if not conversation.staff_user_id:
        conversation.staff_user = user
        update_fields.append("staff_user")
    conversation.save(update_fields=update_fields)


def tenant_name(handover):
    if handover.tenant:
        return handover.tenant.get_full_name() or str(handover.tenant)
    return "Tenant"

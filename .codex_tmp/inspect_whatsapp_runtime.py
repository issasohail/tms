from django.utils import timezone

from whatsapp.models import WhatsAppMessageLog, WhatsAppWebhookLog


print("NOW", timezone.localtime())
print("WEBHOOKS", WhatsAppWebhookLog.objects.count())
for item in WhatsAppWebhookLog.objects.order_by("-created_at")[:15]:
    print("W", item.pk, timezone.localtime(item.created_at).isoformat(), item.event_type, item.method)

print("MESSAGES", WhatsAppMessageLog.objects.count())
for item in WhatsAppMessageLog.objects.order_by("-created_at")[:25]:
    print(
        "M",
        item.pk,
        timezone.localtime(item.created_at).isoformat(),
        item.direction,
        item.message_type,
        item.status,
        (item.error_text or "")[:120],
    )

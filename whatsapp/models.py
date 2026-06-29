from django.conf import settings
from django.db import models


class WhatsAppMessageLog(models.Model):
    DIRECTION_OUTBOUND = "outbound"
    DIRECTION_INBOUND = "inbound"
    DIRECTION_STATUS = "status"

    DIRECTION_CHOICES = [
        (DIRECTION_OUTBOUND, "Outbound"),
        (DIRECTION_INBOUND, "Inbound"),
        (DIRECTION_STATUS, "Status"),
    ]

    STATUS_PENDING = "pending"
    STATUS_QUEUED = "queued"
    STATUS_SCHEDULED = "scheduled"
    STATUS_SENT = "sent"
    STATUS_DELIVERED = "delivered"
    STATUS_READ = "read"
    STATUS_FAILED = "failed"
    STATUS_RECEIVED = "received"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_QUEUED, "Queued"),
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_SENT, "Sent"),
        (STATUS_DELIVERED, "Delivered"),
        (STATUS_READ, "Read"),
        (STATUS_FAILED, "Failed"),
        (STATUS_RECEIVED, "Received"),
    ]

    MESSAGE_TYPE_TEXT = "text"
    MESSAGE_TYPE_TEMPLATE = "template"
    MESSAGE_TYPE_DOCUMENT = "document"
    MESSAGE_TYPE_IMAGE = "image"
    MESSAGE_TYPE_PDF = "pdf"
    MESSAGE_TYPE_WEBHOOK = "webhook"
    MESSAGE_TYPE_STATUS = "status"

    MESSAGE_TYPE_CHOICES = [
        (MESSAGE_TYPE_TEXT, "Text"),
        (MESSAGE_TYPE_TEMPLATE, "Template"),
        (MESSAGE_TYPE_DOCUMENT, "Document"),
        (MESSAGE_TYPE_IMAGE, "Image"),
        (MESSAGE_TYPE_PDF, "PDF"),
        (MESSAGE_TYPE_WEBHOOK, "Webhook"),
        (MESSAGE_TYPE_STATUS, "Status"),
    ]

    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_messages",
    )
    lease = models.ForeignKey(
        "leases.Lease",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_messages",
    )
    invoice = models.ForeignKey(
        "invoices.Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_messages",
    )
    payment = models.ForeignKey(
        "payments.Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_messages",
    )
    maintenance_request = models.ForeignKey(
        "maintenance.MaintenanceRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_messages",
    )
    phone_number = models.CharField(max_length=32, blank=True)
    conversation_id = models.CharField(max_length=120, blank=True)
    wa_message_id = models.CharField(max_length=160, blank=True, db_index=True)
    template_name = models.CharField(max_length=120, blank=True)
    message_type = models.CharField(max_length=30, choices=MESSAGE_TYPE_CHOICES, default=MESSAGE_TYPE_TEXT)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING)
    payload = models.JSONField(default=dict, blank=True)
    api_response = models.JSONField(default=dict, blank=True)
    error_text = models.TextField(blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_whatsapp_messages",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["direction", "status"]),
            models.Index(fields=["phone_number", "created_at"]),
            models.Index(fields=["scheduled_for"]),
        ]

    def __str__(self):
        return f"{self.direction} {self.message_type} to {self.phone_number or '-'} ({self.status})"

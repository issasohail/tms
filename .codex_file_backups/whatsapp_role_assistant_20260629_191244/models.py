from django.conf import settings
from django.db import models
from django.utils import timezone


def whatsapp_pending_upload_to(instance, filename):
    folder = timezone.localtime().strftime("%Y/%m")
    return f"whatsapp/pending/{folder}/{filename}"


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


class WhatsAppWebhookLog(models.Model):
    event_type = models.CharField(max_length=80, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    headers = models.JSONField(default=dict, blank=True)
    method = models.CharField(max_length=10, blank=True)
    remote_addr = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.method or 'POST'} {self.event_type or 'webhook'} at {self.created_at}"


class WhatsAppConversation(models.Model):
    STATUS_OPEN = "open"
    STATUS_PENDING_ADMIN = "pending_admin"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_PENDING_ADMIN, "Pending admin"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    phone_number = models.CharField(max_length=32, unique=True, db_index=True)
    selected_lease = models.ForeignKey(
        "leases.Lease",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_conversations",
    )
    selected_property = models.ForeignKey(
        "properties.Property",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_conversations",
    )
    selected_unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_conversations",
    )
    pending_state = models.CharField(max_length=80, blank=True)
    context = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    last_message_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_message_at", "-updated_at"]

    def __str__(self):
        return self.phone_number


class WhatsAppAIInteractionLog(models.Model):
    conversation = models.ForeignKey(
        WhatsAppConversation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_logs",
    )
    message_log = models.ForeignKey(
        WhatsAppMessageLog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_logs",
    )
    phone_number = models.CharField(max_length=32, blank=True, db_index=True)
    intent = models.CharField(max_length=80, blank=True)
    ai_prompt = models.TextField(blank=True)
    ai_response = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    estimated_cost = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    error_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone_number or '-'} {self.intent or 'ai'}"


class PendingWhatsAppPayment(models.Model):
    STATUS_PENDING = "pending"
    STATUS_CONFIRMED = "confirmed"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_CONFIRMED, "Confirmed by tenant"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.SET_NULL, null=True, blank=True)
    lease = models.ForeignKey("leases.Lease", on_delete=models.SET_NULL, null=True, blank=True)
    property = models.ForeignKey("properties.Property", on_delete=models.SET_NULL, null=True, blank=True)
    unit = models.ForeignKey("properties.Unit", on_delete=models.SET_NULL, null=True, blank=True)
    phone = models.CharField(max_length=32, blank=True, db_index=True)
    screenshot = models.FileField(upload_to=whatsapp_pending_upload_to, blank=True, null=True, max_length=255)
    ocr_json = models.JSONField(default=dict, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    date = models.DateField(null=True, blank=True)
    reference = models.CharField(max_length=160, blank=True)
    bank_information = models.JSONField(default=dict, blank=True)
    ai_confidence = models.PositiveSmallIntegerField(default=0)
    ai_notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    confirmed_by_tenant = models.BooleanField(default=False)
    approved = models.BooleanField(default=False)
    rejected = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_whatsapp_payments",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_payment = models.ForeignKey(
        "payments.Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_pending_sources",
    )
    original_whatsapp_message = models.ForeignKey(
        WhatsAppMessageLog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_payments",
    )
    conversation = models.ForeignKey(
        WhatsAppConversation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_payments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["phone", "created_at"]),
        ]

    def __str__(self):
        return f"Pending payment {self.phone or '-'} {self.amount or ''}"


class PendingWhatsAppMedia(models.Model):
    PURPOSE_PROPERTY = "property"
    PURPOSE_UNIT = "unit"
    PURPOSE_LEASE = "lease"
    PURPOSE_MAINTENANCE = "maintenance"
    PURPOSE_PAYMENT = "payment"
    PURPOSE_OTHER = "other"
    PURPOSE_CHOICES = [
        (PURPOSE_PROPERTY, "Property Photos"),
        (PURPOSE_UNIT, "Unit Photos"),
        (PURPOSE_LEASE, "Lease Documents"),
        (PURPOSE_MAINTENANCE, "Maintenance"),
        (PURPOSE_PAYMENT, "Payment"),
        (PURPOSE_OTHER, "Other"),
    ]
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    conversation = models.ForeignKey(WhatsAppConversation, on_delete=models.SET_NULL, null=True, blank=True, related_name="pending_media")
    original_whatsapp_message = models.ForeignKey(WhatsAppMessageLog, on_delete=models.SET_NULL, null=True, blank=True, related_name="pending_media")
    phone = models.CharField(max_length=32, blank=True, db_index=True)
    file = models.FileField(upload_to=whatsapp_pending_upload_to, max_length=255)
    original_filename = models.CharField(max_length=255, blank=True)
    media_type = models.CharField(max_length=30, blank=True)
    whatsapp_media_id = models.CharField(max_length=160, blank=True)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default=PURPOSE_OTHER)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.SET_NULL, null=True, blank=True)
    lease = models.ForeignKey("leases.Lease", on_delete=models.SET_NULL, null=True, blank=True)
    property = models.ForeignKey("properties.Property", on_delete=models.SET_NULL, null=True, blank=True)
    unit = models.ForeignKey("properties.Unit", on_delete=models.SET_NULL, null=True, blank=True)
    ai_confidence = models.PositiveSmallIntegerField(default=0)
    ai_notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Pending media {self.phone or '-'} {self.purpose}"


class PendingWhatsAppMaintenance(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    conversation = models.ForeignKey(WhatsAppConversation, on_delete=models.SET_NULL, null=True, blank=True, related_name="pending_maintenance")
    original_whatsapp_message = models.ForeignKey(WhatsAppMessageLog, on_delete=models.SET_NULL, null=True, blank=True, related_name="pending_maintenance")
    phone = models.CharField(max_length=32, blank=True, db_index=True)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.SET_NULL, null=True, blank=True)
    lease = models.ForeignKey("leases.Lease", on_delete=models.SET_NULL, null=True, blank=True)
    property = models.ForeignKey("properties.Property", on_delete=models.SET_NULL, null=True, blank=True)
    unit = models.ForeignKey("properties.Unit", on_delete=models.SET_NULL, null=True, blank=True)
    issue_type = models.CharField(max_length=80, blank=True)
    urgency = models.CharField(max_length=20, blank=True)
    description = models.TextField(blank=True)
    media = models.ManyToManyField(PendingWhatsAppMedia, blank=True, related_name="maintenance_submissions")
    ai_confidence = models.PositiveSmallIntegerField(default=0)
    ai_notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_request = models.ForeignKey(
        "maintenance.MaintenanceRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_pending_sources",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Pending maintenance {self.phone or '-'} {self.issue_type or 'issue'}"

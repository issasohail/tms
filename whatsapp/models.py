import builtins
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from core.model_fields import NormalizedPhoneField


def whatsapp_pending_upload_to(instance, filename):
    folder = timezone.localtime().strftime("%Y/%m")
    return f"whatsapp/pending/{folder}/{filename}"


def whatsapp_external_link_token():
    return uuid.uuid4().hex + uuid.uuid4().hex


def whatsapp_handover_reference():
    return f"WH-{timezone.localdate():%Y}-{uuid.uuid4().hex[:8].upper()}"


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
    MESSAGE_TYPE_AUDIO = "audio"
    MESSAGE_TYPE_VIDEO = "video"
    MESSAGE_TYPE_PDF = "pdf"
    MESSAGE_TYPE_WEBHOOK = "webhook"
    MESSAGE_TYPE_STATUS = "status"

    MESSAGE_TYPE_CHOICES = [
        (MESSAGE_TYPE_TEXT, "Text"),
        (MESSAGE_TYPE_TEMPLATE, "Template"),
        (MESSAGE_TYPE_DOCUMENT, "Document"),
        (MESSAGE_TYPE_IMAGE, "Image"),
        (MESSAGE_TYPE_AUDIO, "Audio"),
        (MESSAGE_TYPE_VIDEO, "Video"),
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
    phone_number = NormalizedPhoneField(max_length=32, blank=True)
    conversation_id = models.CharField(max_length=120, blank=True)
    wa_message_id = models.CharField(max_length=160, blank=True, db_index=True)
    template_name = models.CharField(max_length=120, blank=True)
    message_type = models.CharField(max_length=30, choices=MESSAGE_TYPE_CHOICES, default=MESSAGE_TYPE_TEXT)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING)
    body_parameters = models.JSONField(default=list, blank=True)
    button_parameter = models.CharField(max_length=500, blank=True)
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


class WhatsAppUtilityTemplate(models.Model):
    LANGUAGE_CHOICES = [
        ("en", "English (en)"),
        ("en_US", "English - United States (en_US)"),
        ("en_GB", "English - United Kingdom (en_GB)"),
        ("ur", "Urdu (ur)"),
        ("ar", "Arabic (ar)"),
    ]

    TEMPLATE_CHOICES = [
        ("invoice_notice", "Invoice notice"),
        ("payment_confirmation", "Payment confirmation"),
        ("balance_reminder", "Balance reminder"),
        ("late_fee_reminder", "Late fee reminder"),
        ("lease_ledger_link", "Lease ledger link"),
        ("rent_due_reminder", "Rent due reminder"),
        ("lease_expiry_notice", "Lease expiry notice"),
        ("lease_renewal_offer", "Lease renewal offer"),
        ("maintenance_request_received", "Maintenance request received"),
        ("maintenance_update", "Maintenance update"),
        ("maintenance_completed", "Maintenance completed"),
        ("inspection_schedule", "Inspection schedule"),
        ("inspection_report_ready", "Inspection report ready"),
        ("agreement_ready", "Agreement ready"),
        ("tenant_registration", "Tenant registration"),
        ("lease_application", "Lease application"),
        ("meter_reading_available", "Meter reading available"),
        ("water_bill_added", "Water bill added"),
        ("account_notice", "Account notice"),
    ]

    key = models.CharField(max_length=80, choices=TEMPLATE_CHOICES, unique=True)
    template_name = models.CharField(
        max_length=120,
        help_text="Approved Meta template name. Usually the same as the key.",
    )
    language_code = models.CharField(max_length=20, choices=LANGUAGE_CHOICES, default="en")
    body_text = models.TextField(
        blank=True,
        help_text="Local reference copy of the approved Meta body. Changing this does not update Meta automatically.",
    )
    body_variables = models.JSONField(default=list, blank=True)
    button_label = models.CharField(max_length=120, blank=True)
    button_parameter_source = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.template_name or self.key

    def save(self, *args, **kwargs):
        if not self.template_name:
            self.template_name = self.key
        super().save(*args, **kwargs)


class WhatsAppConversation(models.Model):
    STATUS_OPEN = "open"
    STATUS_PENDING_ADMIN = "pending_admin"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_PENDING_ADMIN, "Pending admin"),
        (STATUS_ARCHIVED, "Archived"),
    ]
    MODE_GUEST = "guest"
    MODE_TENANT = "tenant"
    MODE_STAFF = "staff"
    MODE_HANDOVER = "handover"
    MODE_CHOICES = [
        (MODE_GUEST, "Guest"),
        (MODE_TENANT, "Tenant"),
        (MODE_STAFF, "Staff"),
        (MODE_HANDOVER, "Handover"),
    ]

    phone_number = NormalizedPhoneField(max_length=32, unique=True, db_index=True)
    selected_mode = models.CharField(max_length=20, choices=MODE_CHOICES, blank=True)
    mode_expires_at = models.DateTimeField(null=True, blank=True)
    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_conversations",
    )
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_conversations",
    )
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
    ai_enabled = models.BooleanField(default=True)
    handover_active = models.BooleanField(default=False)
    assigned_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_whatsapp_conversations",
    )
    last_ai_confidence = models.PositiveSmallIntegerField(default=0)
    preferred_language = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_inbound_message_at = models.DateTimeField(null=True, blank=True)
    last_inbound_message_id = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_message_at", "-updated_at"]

    def __str__(self):
        return self.phone_number

    @property
    def selected_mode_is_valid(self):
        return bool(self.selected_mode and self.mode_expires_at and self.mode_expires_at > timezone.now())


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
    phone_number = NormalizedPhoneField(max_length=32, blank=True, db_index=True)
    intent = models.CharField(max_length=80, blank=True)
    model = models.CharField(max_length=80, blank=True)
    provider = models.CharField(max_length=30, blank=True)
    input_summary = models.TextField(blank=True)
    decision_json = models.JSONField(default=dict, blank=True)
    tool_calls = models.JSONField(default=list, blank=True)
    tool_results_summary = models.JSONField(default=list, blank=True)
    confidence = models.PositiveSmallIntegerField(default=0)
    language = models.CharField(max_length=20, blank=True)
    fallback_used = models.BooleanField(default=False)
    handover_triggered = models.BooleanField(default=False)
    handover_reason = models.CharField(max_length=160, blank=True)
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
    phone = NormalizedPhoneField(max_length=32, blank=True, db_index=True)
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
    phone = NormalizedPhoneField(max_length=32, blank=True, db_index=True)
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
    phone = NormalizedPhoneField(max_length=32, blank=True, db_index=True)
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


class WhatsAppStaffPropertyAccess(models.Model):
    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="whatsapp_property_access",
    )
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="whatsapp_staff_access",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("staff_user", "property")]
        ordering = ["staff_user__username", "property__property_name"]

    def __str__(self):
        return f"{self.staff_user} - {self.property}"


class WhatsAppStaffActionLog(models.Model):
    ACTION_STATUS_ALLOWED = "allowed"
    ACTION_STATUS_BLOCKED = "blocked"
    ACTION_STATUS_PENDING = "pending"
    ACTION_STATUS_CHOICES = [
        (ACTION_STATUS_ALLOWED, "Allowed"),
        (ACTION_STATUS_BLOCKED, "Blocked"),
        (ACTION_STATUS_PENDING, "Pending"),
    ]

    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_staff_action_logs",
    )
    phone_number = NormalizedPhoneField(max_length=32, blank=True, db_index=True)
    role_name = models.CharField(max_length=80, blank=True)
    selected_mode = models.CharField(max_length=20, blank=True)
    action = models.CharField(max_length=120)
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_staff_action_logs",
    )
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.SET_NULL, null=True, blank=True)
    lease = models.ForeignKey("leases.Lease", on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=ACTION_STATUS_CHOICES, default=ACTION_STATUS_PENDING)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["staff_user", "created_at"]),
            models.Index(fields=["phone_number", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.staff_user or self.phone_number} {self.action} ({self.status})"


class WhatsAppHandover(models.Model):
    STATUS_NEW = "new"
    STATUS_NOTIFIED = "notified"
    STATUS_ACCEPTED = "accepted"
    STATUS_WAITING_FOR_TENANT = "waiting_for_tenant"
    STATUS_WAITING_FOR_STAFF = "waiting_for_staff"
    STATUS_CALL_REQUESTED = "call_requested"
    STATUS_CALLED = "called"
    STATUS_RESOLVED = "resolved"
    STATUS_CLOSED = "closed"
    STATUS_RETURNED_TO_AI = "returned_to_ai"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_NEW, "New"),
        (STATUS_NOTIFIED, "Notified"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_WAITING_FOR_TENANT, "Waiting for tenant"),
        (STATUS_WAITING_FOR_STAFF, "Waiting for staff"),
        (STATUS_CALL_REQUESTED, "Call requested"),
        (STATUS_CALLED, "Called"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_CLOSED, "Closed"),
        (STATUS_RETURNED_TO_AI, "Returned to AI"),
        (STATUS_CANCELLED, "Cancelled"),
    ]
    ACTIVE_STATUSES = {
        STATUS_NEW,
        STATUS_NOTIFIED,
        STATUS_ACCEPTED,
        STATUS_WAITING_FOR_TENANT,
        STATUS_WAITING_FOR_STAFF,
        STATUS_CALL_REQUESTED,
        STATUS_CALLED,
    }
    PRIORITY_LOW = "low"
    PRIORITY_NORMAL = "normal"
    PRIORITY_HIGH = "high"
    PRIORITY_URGENT = "urgent"
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "Low"),
        (PRIORITY_NORMAL, "Normal"),
        (PRIORITY_HIGH, "High"),
        (PRIORITY_URGENT, "Urgent"),
    ]

    reference = models.CharField(max_length=24, unique=True, default=whatsapp_handover_reference)
    conversation = models.ForeignKey(
        WhatsAppConversation, on_delete=models.CASCADE, related_name="handovers"
    )
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.SET_NULL, null=True, blank=True)
    lease = models.ForeignKey("leases.Lease", on_delete=models.SET_NULL, null=True, blank=True)
    property = models.ForeignKey("properties.Property", on_delete=models.SET_NULL, null=True, blank=True)
    unit = models.ForeignKey("properties.Unit", on_delete=models.SET_NULL, null=True, blank=True)
    tenant_phone = NormalizedPhoneField(max_length=32, db_index=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_NEW, db_index=True)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL)
    department = models.CharField(max_length=30, blank=True)
    reason = models.CharField(max_length=160)
    tenant_message = models.TextField(blank=True)
    ai_summary = models.TextField(blank=True)
    assigned_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_whatsapp_handovers",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    last_staff_message_at = models.DateTimeField(null=True, blank=True)
    last_tenant_message_at = models.DateTimeField(null=True, blank=True)
    call_requested_at = models.DateTimeField(null=True, blank=True)
    called_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    returned_to_ai_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["status", "priority", "created_at"]),
            models.Index(fields=["assigned_staff", "status"]),
            models.Index(fields=["property", "status"]),
        ]

    def __str__(self):
        return f"{self.reference} ({self.status})"

    @builtins.property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES


class WhatsAppHandoverMessage(models.Model):
    SENDER_TENANT = "tenant"
    SENDER_STAFF = "staff"
    SENDER_SYSTEM = "system"
    SENDER_CHOICES = [
        (SENDER_TENANT, "Tenant"),
        (SENDER_STAFF, "Staff"),
        (SENDER_SYSTEM, "System"),
    ]
    DIRECTION_INBOUND = "inbound"
    DIRECTION_RELAYED = "relayed"
    DIRECTION_INTERNAL = "internal"
    DIRECTION_CHOICES = [
        (DIRECTION_INBOUND, "Inbound"),
        (DIRECTION_RELAYED, "Relayed"),
        (DIRECTION_INTERNAL, "Internal"),
    ]

    handover = models.ForeignKey(WhatsAppHandover, on_delete=models.CASCADE, related_name="messages")
    source_message = models.ForeignKey(
        WhatsAppMessageLog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handover_source_messages",
    )
    relayed_message = models.ForeignKey(
        WhatsAppMessageLog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handover_relayed_messages",
    )
    sender_type = models.CharField(max_length=20, choices=SENDER_CHOICES)
    staff_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    original_text = models.TextField(blank=True)
    relayed_text = models.TextField(blank=True)
    media = models.ForeignKey(PendingWhatsAppMedia, on_delete=models.SET_NULL, null=True, blank=True)
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES, default=DIRECTION_INBOUND)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class WhatsAppStaffRoutingRule(models.Model):
    DEPARTMENT_GENERAL = "general"
    DEPARTMENT_ACCOUNTS = "accounts"
    DEPARTMENT_MAINTENANCE = "maintenance"
    DEPARTMENT_LEASING = "leasing"
    DEPARTMENT_MANAGEMENT = "management"
    DEPARTMENT_CHOICES = [
        (DEPARTMENT_GENERAL, "General support"),
        (DEPARTMENT_ACCOUNTS, "Accounts"),
        (DEPARTMENT_MAINTENANCE, "Maintenance"),
        (DEPARTMENT_LEASING, "Leasing"),
        (DEPARTMENT_MANAGEMENT, "Management"),
    ]

    property = models.ForeignKey("properties.Property", on_delete=models.CASCADE, null=True, blank=True)
    category = models.CharField(max_length=80, blank=True)
    department = models.CharField(max_length=30, choices=DEPARTMENT_CHOICES, default=DEPARTMENT_GENERAL)
    staff_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    priority = models.PositiveSmallIntegerField(default=50)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "department", "staff_user"],
                name="unique_whatsapp_staff_route",
            )
        ]


class WhatsAppExternalLinkToken(models.Model):
    LINK_TENANT_REGISTRATION = "tenant_registration"
    LINK_LEASE_CREATION = "lease_creation"
    LINK_AGREEMENT_VIEW = "agreement_view"
    LINK_AGREEMENT_EDIT = "agreement_edit"
    LINK_INVOICE_VIEW = "invoice_view"
    LINK_INVOICE_PDF = "invoice_pdf"
    LINK_LEDGER_VIEW = "ledger_view"
    LINK_LEASE_FAMILY_ADD = "lease_family_add"
    LINK_POLICE_VERIFICATION = "police_verification"
    LINK_PAYMENT_RECEIPT_UPLOAD = "payment_receipt_upload"
    LINK_MAINTENANCE_PHOTO_UPLOAD = "maintenance_photo_upload"
    LINK_TYPE_CHOICES = [
        (LINK_TENANT_REGISTRATION, "Tenant registration form"),
        (LINK_LEASE_CREATION, "Lease creation form"),
        (LINK_AGREEMENT_VIEW, "Agreement view link"),
        (LINK_AGREEMENT_EDIT, "Agreement edit link"),
        (LINK_INVOICE_VIEW, "Invoice view link"),
        (LINK_INVOICE_PDF, "Invoice PDF link"),
        (LINK_LEDGER_VIEW, "Ledger view link"),
        (LINK_LEASE_FAMILY_ADD, "Lease family member form"),
        (LINK_POLICE_VERIFICATION, "Police verification form"),
        (LINK_PAYMENT_RECEIPT_UPLOAD, "Payment receipt upload"),
        (LINK_MAINTENANCE_PHOTO_UPLOAD, "Maintenance photo upload"),
    ]

    token = models.CharField(max_length=64, unique=True, default=whatsapp_external_link_token)
    link_type = models.CharField(max_length=40, choices=LINK_TYPE_CHOICES)
    phone_number = NormalizedPhoneField(max_length=32, blank=True, db_index=True)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.SET_NULL, null=True, blank=True)
    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_external_links_created",
    )
    target_app_label = models.CharField(max_length=80, blank=True)
    target_model = models.CharField(max_length=80, blank=True)
    target_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["token", "expires_at", "is_active"]),
            models.Index(fields=["link_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.link_type} {self.token[:8]}"

    @property
    def is_valid(self):
        return self.is_active and self.expires_at >= timezone.now()


class TrustedDeviceRegistry(models.Model):
    USER_TYPE_TENANT = "tenant"
    USER_TYPE_STAFF = "staff"
    USER_TYPE_GUEST = "guest"
    USER_TYPE_CHOICES = [
        (USER_TYPE_TENANT, "Tenant"),
        (USER_TYPE_STAFF, "Staff"),
        (USER_TYPE_GUEST, "Guest"),
    ]
    TRUSTED_PENDING = "pending"
    TRUSTED_ACTIVE = "active"
    TRUSTED_BLOCKED = "blocked"
    TRUSTED_STATUS_CHOICES = [
        (TRUSTED_PENDING, "Pending"),
        (TRUSTED_ACTIVE, "Active"),
        (TRUSTED_BLOCKED, "Blocked"),
    ]

    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, default=USER_TYPE_GUEST)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.SET_NULL, null=True, blank=True)
    staff_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    phone_number = NormalizedPhoneField(max_length=32, blank=True, db_index=True)
    whatsapp_id = models.CharField(max_length=80, blank=True, db_index=True)
    device_name = models.CharField(max_length=120, blank=True)
    mac_address = models.CharField(max_length=80, blank=True, null=True)
    browser_fingerprint = models.CharField(max_length=160, blank=True, null=True, db_index=True)
    operating_system = models.CharField(max_length=120, blank=True)
    browser = models.CharField(max_length=120, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    otp_verified = models.BooleanField(default=False)
    trusted_status = models.CharField(max_length=20, choices=TRUSTED_STATUS_CHOICES, default=TRUSTED_PENDING)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-last_seen"]
        indexes = [
            models.Index(fields=["phone_number", "last_seen"]),
            models.Index(fields=["trusted_status", "last_seen"]),
        ]

    def __str__(self):
        return f"{self.user_type} {self.phone_number or self.whatsapp_id or self.pk}"

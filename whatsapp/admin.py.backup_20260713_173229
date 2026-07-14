from django.contrib import admin
from django.core.files.base import ContentFile
from django.utils import timezone

from leases.models import LeaseDocument
from maintenance.models import MaintenanceRequest, MaintenanceRequestMedia
from payments.models import Payment
from properties.models import PropertyMedia, UnitMedia

from .models import (
    PendingWhatsAppMaintenance,
    PendingWhatsAppMedia,
    PendingWhatsAppPayment,
    TrustedDeviceRegistry,
    WhatsAppAIInteractionLog,
    WhatsAppConversation,
    WhatsAppExternalLinkToken,
    WhatsAppMessageLog,
    WhatsAppStaffActionLog,
    WhatsAppStaffPropertyAccess,
    WhatsAppUtilityTemplate,
    WhatsAppWebhookLog,
)


@admin.register(WhatsAppMessageLog)
class WhatsAppMessageLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "direction",
        "message_type",
        "status",
        "phone_number",
        "tenant",
        "template_name",
        "created_at",
    )
    list_filter = ("direction", "message_type", "status", "created_at")
    search_fields = (
        "phone_number",
        "wa_message_id",
        "conversation_id",
        "template_name",
        "tenant__first_name",
        "tenant__last_name",
    )
    readonly_fields = ("created_at", "updated_at")
    fields = (
        "direction",
        "message_type",
        "status",
        "phone_number",
        "tenant",
        "lease",
        "invoice",
        "payment",
        "maintenance_request",
        "template_name",
        "body_parameters",
        "button_parameter",
        "wa_message_id",
        "conversation_id",
        "payload",
        "api_response",
        "error_text",
        "retry_count",
        "scheduled_for",
        "created_by",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("tenant", "lease", "invoice", "payment", "maintenance_request", "created_by")


@admin.register(WhatsAppWebhookLog)
class WhatsAppWebhookLogAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "method", "remote_addr", "created_at")
    list_filter = ("event_type", "method", "created_at")
    search_fields = ("event_type", "remote_addr")
    readonly_fields = ("event_type", "payload", "headers", "method", "remote_addr", "created_at")


@admin.register(WhatsAppUtilityTemplate)
class WhatsAppUtilityTemplateAdmin(admin.ModelAdmin):
    list_display = ("key", "template_name", "language_code", "button_label", "is_active", "updated_at")
    list_filter = ("is_active", "language_code")
    search_fields = ("key", "template_name", "body_text", "button_label", "notes")
    readonly_fields = ("created_at", "updated_at")


@admin.register(WhatsAppConversation)
class WhatsAppConversationAdmin(admin.ModelAdmin):
    list_display = ("phone_number", "selected_mode", "staff_user", "tenant", "selected_lease", "pending_state", "status", "last_inbound_message_at", "last_message_at", "updated_at")
    list_filter = ("selected_mode", "status", "pending_state", "updated_at")
    search_fields = ("phone_number", "selected_lease__tenant__first_name", "selected_lease__tenant__last_name")
    raw_id_fields = ("staff_user", "tenant", "selected_lease", "selected_property", "selected_unit")
    readonly_fields = ("created_at", "updated_at")


@admin.register(WhatsAppAIInteractionLog)
class WhatsAppAIInteractionLogAdmin(admin.ModelAdmin):
    list_display = ("id", "phone_number", "intent", "latency_ms", "created_at")
    list_filter = ("intent", "created_at")
    search_fields = ("phone_number", "intent", "ai_response", "error_text")
    raw_id_fields = ("conversation", "message_log")
    readonly_fields = (
        "conversation",
        "message_log",
        "phone_number",
        "intent",
        "ai_prompt",
        "ai_response",
        "metadata",
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "estimated_cost",
        "error_text",
        "created_at",
    )


@admin.register(PendingWhatsAppPayment)
class PendingWhatsAppPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "phone",
        "tenant",
        "property",
        "unit",
        "amount",
        "date",
        "ai_confidence",
        "status",
        "approved",
        "created_at",
    )
    list_filter = ("status", "approved", "rejected", "created_at", "property")
    search_fields = ("phone", "tenant__first_name", "tenant__last_name", "reference", "ai_notes")
    raw_id_fields = ("tenant", "lease", "property", "unit", "original_whatsapp_message", "conversation", "created_payment", "approved_by")
    readonly_fields = ("created_at", "updated_at", "approved_at")
    actions = ("approve_payments", "reject_payments")

    @admin.action(description="Approve selected pending payments and create real payments")
    def approve_payments(self, request, queryset):
        created = 0
        skipped = 0
        for pending in queryset:
            if pending.approved or pending.rejected or not pending.lease_id or not pending.amount:
                skipped += 1
                continue
            payment = Payment.objects.create(
                lease=pending.lease,
                payment_date=pending.date or timezone.localdate(),
                amount=pending.amount,
                reference_number=pending.reference or "",
                notes=f"Created from WhatsApp pending payment #{pending.pk}. {pending.ai_notes}",
            )
            pending.created_payment = payment
            pending.approved = True
            pending.rejected = False
            pending.status = PendingWhatsAppPayment.STATUS_APPROVED
            pending.approved_by = request.user
            pending.approved_at = timezone.now()
            pending.save(update_fields=[
                "created_payment",
                "approved",
                "rejected",
                "status",
                "approved_by",
                "approved_at",
                "updated_at",
            ])
            created += 1
        self.message_user(request, f"Approved {created} payment(s). Skipped {skipped}.")

    @admin.action(description="Reject selected pending payments")
    def reject_payments(self, request, queryset):
        updated = queryset.filter(approved=False).update(
            rejected=True,
            status=PendingWhatsAppPayment.STATUS_REJECTED,
            updated_at=timezone.now(),
        )
        self.message_user(request, f"Rejected {updated} pending payment(s).")


@admin.register(PendingWhatsAppMedia)
class PendingWhatsAppMediaAdmin(admin.ModelAdmin):
    list_display = ("id", "phone", "purpose", "property", "unit", "lease", "status", "ai_confidence", "created_at")
    list_filter = ("purpose", "status", "created_at", "property")
    search_fields = ("phone", "original_filename", "ai_notes")
    raw_id_fields = ("conversation", "original_whatsapp_message", "tenant", "lease", "property", "unit", "approved_by")
    readonly_fields = ("created_at", "updated_at", "approved_at")
    actions = ("approve_media", "reject_media")

    @admin.action(description="Approve selected media and attach to selected record")
    def approve_media(self, request, queryset):
        attached = 0
        skipped = 0
        for pending in queryset:
            if pending.status != PendingWhatsAppMedia.STATUS_PENDING or not pending.file:
                skipped += 1
                continue
            try:
                _attach_pending_media(pending, request.user)
            except ValueError:
                skipped += 1
                continue
            pending.status = PendingWhatsAppMedia.STATUS_APPROVED
            pending.approved_by = request.user
            pending.approved_at = timezone.now()
            pending.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
            attached += 1
        self.message_user(request, f"Attached {attached} media file(s). Skipped {skipped}.")

    @admin.action(description="Reject selected pending media")
    def reject_media(self, request, queryset):
        updated = queryset.filter(status=PendingWhatsAppMedia.STATUS_PENDING).update(
            status=PendingWhatsAppMedia.STATUS_REJECTED,
            updated_at=timezone.now(),
        )
        self.message_user(request, f"Rejected {updated} pending media file(s).")


@admin.register(PendingWhatsAppMaintenance)
class PendingWhatsAppMaintenanceAdmin(admin.ModelAdmin):
    list_display = ("id", "phone", "issue_type", "urgency", "tenant", "unit", "status", "ai_confidence", "created_at")
    list_filter = ("status", "urgency", "issue_type", "created_at")
    search_fields = ("phone", "description", "ai_notes")
    raw_id_fields = ("conversation", "original_whatsapp_message", "tenant", "lease", "property", "unit", "created_request", "approved_by")
    readonly_fields = ("created_at", "updated_at", "approved_at")
    actions = ("approve_maintenance", "reject_maintenance")

    @admin.action(description="Approve selected maintenance submissions and create tickets")
    def approve_maintenance(self, request, queryset):
        created = 0
        skipped = 0
        for pending in queryset:
            if pending.status != PendingWhatsAppMaintenance.STATUS_PENDING or not pending.unit_id:
                skipped += 1
                continue
            ticket = MaintenanceRequest.objects.create(
                lease=pending.lease,
                unit=pending.unit,
                tenant=pending.tenant,
                title=pending.issue_type or "WhatsApp Maintenance",
                description=pending.description,
                source=MaintenanceRequest.SOURCE_MANUAL,
                category=pending.issue_type or "General",
                priority="urgent" if pending.urgency in {"urgent", "emergency"} else "normal",
                created_by=request.user,
            )
            for media in pending.media.all():
                if not media.file:
                    continue
                media.file.open("rb")
                MaintenanceRequestMedia.objects.create(
                    request=ticket,
                    file=ContentFile(media.file.read(), name=media.original_filename or media.file.name),
                    description=media.ai_notes[:255],
                    uploaded_by=request.user,
                    original_filename=media.original_filename,
                )
                media.file.close()
            pending.created_request = ticket
            pending.status = PendingWhatsAppMaintenance.STATUS_APPROVED
            pending.approved_by = request.user
            pending.approved_at = timezone.now()
            pending.save(update_fields=["created_request", "status", "approved_by", "approved_at", "updated_at"])
            created += 1
        self.message_user(request, f"Created {created} maintenance ticket(s). Skipped {skipped}.")

    @admin.action(description="Reject selected maintenance submissions")
    def reject_maintenance(self, request, queryset):
        updated = queryset.filter(status=PendingWhatsAppMaintenance.STATUS_PENDING).update(
            status=PendingWhatsAppMaintenance.STATUS_REJECTED,
            updated_at=timezone.now(),
        )
        self.message_user(request, f"Rejected {updated} maintenance submission(s).")


@admin.register(WhatsAppStaffPropertyAccess)
class WhatsAppStaffPropertyAccessAdmin(admin.ModelAdmin):
    list_display = ("staff_user", "property", "is_active", "updated_at")
    list_filter = ("is_active", "property")
    search_fields = ("staff_user__username", "staff_user__first_name", "staff_user__last_name", "property__property_name")
    raw_id_fields = ("staff_user", "property")
    readonly_fields = ("created_at", "updated_at")


@admin.register(WhatsAppStaffActionLog)
class WhatsAppStaffActionLogAdmin(admin.ModelAdmin):
    list_display = ("id", "staff_user", "phone_number", "role_name", "action", "status", "property", "created_at")
    list_filter = ("status", "role_name", "selected_mode", "created_at", "property")
    search_fields = ("phone_number", "staff_user__username", "action", "details")
    raw_id_fields = ("staff_user", "property", "tenant", "lease")
    readonly_fields = (
        "staff_user",
        "phone_number",
        "role_name",
        "selected_mode",
        "action",
        "property",
        "tenant",
        "lease",
        "status",
        "details",
        "created_at",
    )


@admin.register(WhatsAppExternalLinkToken)
class WhatsAppExternalLinkTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "link_type", "phone_number", "tenant", "staff_user", "expires_at", "used_at", "is_active", "created_at")
    list_filter = ("link_type", "is_active", "created_at", "expires_at")
    search_fields = ("token", "phone_number", "tenant__first_name", "tenant__last_name", "staff_user__username")
    raw_id_fields = ("tenant", "staff_user")
    readonly_fields = ("token", "created_at")


@admin.register(TrustedDeviceRegistry)
class TrustedDeviceRegistryAdmin(admin.ModelAdmin):
    list_display = ("id", "user_type", "phone_number", "tenant", "staff_user", "trusted_status", "otp_verified", "last_seen")
    list_filter = ("user_type", "trusted_status", "otp_verified", "last_seen")
    search_fields = ("phone_number", "whatsapp_id", "browser_fingerprint", "tenant__first_name", "tenant__last_name", "staff_user__username")
    raw_id_fields = ("tenant", "staff_user")
    readonly_fields = ("first_seen", "last_seen")


def _attach_pending_media(pending, user):
    pending.file.open("rb")
    content = ContentFile(pending.file.read(), name=pending.original_filename or pending.file.name)
    pending.file.close()
    if pending.purpose == PendingWhatsAppMedia.PURPOSE_PROPERTY and pending.property_id:
        PropertyMedia.objects.create(
            property=pending.property,
            file=content,
            description=pending.ai_notes[:300],
            uploaded_by=user,
            original_filename=pending.original_filename,
        )
        return
    if pending.purpose == PendingWhatsAppMedia.PURPOSE_UNIT and pending.unit_id:
        UnitMedia.objects.create(
            unit=pending.unit,
            file=content,
            description=pending.ai_notes[:300],
            uploaded_by=user,
            original_filename=pending.original_filename,
        )
        return
    if pending.purpose == PendingWhatsAppMedia.PURPOSE_LEASE and pending.lease_id:
        LeaseDocument.objects.create(
            lease=pending.lease,
            file=content,
            original_filename=pending.original_filename,
            display_name=pending.original_filename or "WhatsApp lease document",
            category="other",
            description=pending.ai_notes,
            uploaded_by=user,
        )
        return
    raise ValueError("Pending media has no approved attachment target.")

from django.contrib import admin

from .models import WhatsAppMessageLog


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
    raw_id_fields = ("tenant", "lease", "invoice", "payment", "maintenance_request", "created_by")

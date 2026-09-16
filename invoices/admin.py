from django.contrib import admin
from .models import (
    IescoBillReading,
    IescoHelperDevice,
    IescoHelperPairing,
    IescoStandaloneMeter,
    Invoice,
    InvoiceItem,
    InvoiceLateFeeReminder,
    MonthlyBillingRun,
    MonthlyBillingRunItem,
)


@admin.register(IescoHelperDevice)
class IescoHelperDeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "paired_by", "paired_at", "last_used_at", "is_active", "helper_version")
    list_filter = ("is_active", "paired_at")
    search_fields = ("name", "paired_by__username")
    readonly_fields = ("device_id", "token_hash", "paired_by", "paired_at", "last_used_at", "helper_version", "is_active")
    actions = ("revoke_devices",)

    @admin.action(description="Revoke selected helper devices")
    def revoke_devices(self, request, queryset):
        queryset.update(is_active=False)

    def has_add_permission(self, request):
        return False


@admin.register(IescoHelperPairing)
class IescoHelperPairingAdmin(admin.ModelAdmin):
    change_list_template = "admin/invoices/iescohelperpairing/change_list.html"
    list_display = ("id", "requested_by", "created_at", "expires_at", "used_at", "status", "device")
    list_filter = ("status", "created_at")
    readonly_fields = ("token_hash", "requested_by", "created_at", "expires_at", "used_at", "device", "status")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_urls(self):
        from django.urls import path
        return [
            path("pair/", self.admin_site.admin_view(self.create_from_admin), name="invoices_iescohelperpairing_pair"),
        ] + super().get_urls()

    def create_from_admin(self, request):
        from django.core.exceptions import PermissionDenied
        from django.http import HttpResponseNotAllowed
        from django.http import JsonResponse
        from django.template.response import TemplateResponse
        from .views_iesco_helper import _can_manage, create_pairing_for
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not _can_manage(request.user):
            raise PermissionDenied
        if not request.is_secure():
            return JsonResponse({"error": "Open TMS over HTTPS to create a pairing request."}, status=400)
        pairing, token = create_pairing_for(request.user)
        context = dict(self.admin_site.each_context(request), title="Connect IESCO computer", pairing=pairing, protocol_url=f"tms-iesco://pair?token={token}")
        response = TemplateResponse(request, "admin/invoices/iescohelperpairing/pair.html", context)
        response["Cache-Control"] = "no-store"
        return response


@admin.register(IescoStandaloneMeter)
class IescoStandaloneMeterAdmin(admin.ModelAdmin):
    list_display = ("reference_no", "description", "phone", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("reference_no", "description", "phone")


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 1


class InvoiceLateFeeReminderInline(admin.TabularInline):
    model = InvoiceLateFeeReminder
    extra = 0
    fields = ("reminder_number", "sent_via", "status", "fee_amount", "created_at", "whatsapp_message", "late_fee_item")
    readonly_fields = fields
    can_delete = False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'lease', 'issue_date',
                    'due_date', 'status', 'total_amount')
    list_filter = ('status', 'issue_date')
    search_fields = ('invoice_number', 'lease__tenant__name')
    inlines = [InvoiceItemInline, InvoiceLateFeeReminderInline]
    date_hierarchy = 'issue_date'


@admin.register(InvoiceItem)
class InvoiceItemAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'description', 'amount', 'total')
    list_filter = ('is_recurring',)


class MonthlyBillingRunItemInline(admin.TabularInline):
    model = MonthlyBillingRunItem
    extra = 0
    fields = ("lease", "invoice", "status", "issue_code", "invoice_total", "whatsapp_status", "sent_at")
    readonly_fields = fields
    can_delete = False


@admin.register(MonthlyBillingRun)
class MonthlyBillingRunAdmin(admin.ModelAdmin):
    list_display = (
        "billing_month",
        "run_date",
        "status",
        "total_active_leases",
        "ready_to_send_count",
        "pending_attention_count",
        "sent_count",
        "failed_count",
    )
    list_filter = ("status", "billing_month")
    search_fields = ("notes", "created_by_label")
    date_hierarchy = "billing_month"
    readonly_fields = (
        "total_active_leases",
        "recurring_created_count",
        "missing_recurring_count",
        "electric_ready_count",
        "electric_pending_count",
        "water_missing_count",
        "ready_to_send_count",
        "pending_attention_count",
        "sent_count",
        "failed_count",
        "skipped_count",
        "created_at",
        "updated_at",
    )
    inlines = [MonthlyBillingRunItemInline]


@admin.register(MonthlyBillingRunItem)
class MonthlyBillingRunItemAdmin(admin.ModelAdmin):
    list_display = ("billing_run", "lease", "invoice", "status", "issue_code", "invoice_total", "whatsapp_status")
    list_filter = ("status", "issue_code", "billing_run__billing_month")
    search_fields = ("lease__tenant__first_name", "lease__tenant__last_name", "invoice__invoice_number", "issue_message")
    readonly_fields = ("log", "created_at", "updated_at")


@admin.register(InvoiceLateFeeReminder)
class InvoiceLateFeeReminderAdmin(admin.ModelAdmin):
    list_display = ("invoice", "reminder_number", "sent_via", "status", "fee_amount", "created_at")
    list_filter = ("status", "sent_via")
    search_fields = (
        "invoice__invoice_number",
        "invoice__lease__tenant__first_name",
        "invoice__lease__tenant__last_name",
    )
    readonly_fields = ("created_at", "whatsapp_message", "late_fee_item")


@admin.register(IescoBillReading)
class IescoBillReadingAdmin(admin.ModelAdmin):
    list_display = (
        "reference_no",
        "bill_month",
        "grand_total",
        "payment_status_display",
        "posted_at",
        "due_date",
        "received_at",
        "updated_at",
    )
    list_filter = ("reference_no", "bill_month", "current_month_paid")
    search_fields = ("reference_no", "consumer_id", "consumer_name")
    readonly_fields = tuple(
        field.name for field in IescoBillReading._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

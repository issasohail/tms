from django.contrib import admin
from .models import Invoice, InvoiceItem, MonthlyBillingRun, MonthlyBillingRunItem


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 1


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'lease', 'issue_date',
                    'due_date', 'status', 'total_amount')
    list_filter = ('status', 'issue_date')
    search_fields = ('invoice_number', 'lease__tenant__name')
    inlines = [InvoiceItemInline]
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

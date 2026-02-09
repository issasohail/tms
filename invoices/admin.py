from django.contrib import admin
from .models import Invoice, InvoiceItem


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

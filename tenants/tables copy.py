import django_tables2 as tables
from django_tables2.utils import A
from django.utils.html import format_html
from django.urls import reverse
from .models import Tenant
from django.template.loader import render_to_string
from properties.tables import ExportableTable
from django.utils.safestring import mark_safe
from django_tables2 import Table, Column
from django.utils.html import format_html
from django.urls import reverse
from payments.models import Payment  # Add this import
from invoices.models import Invoice  # Add this if you need to work with invoices
from decimal import Decimal


class TenantTable(ExportableTable):
    # Serial number column
    sno = tables.Column(
        verbose_name='S.No',
        empty_values=(),
        orderable=False,
        attrs={
            'td': {'class': 'text-center'}
        }
    )

    expand = tables.Column(
        verbose_name='',
        empty_values=(),
        orderable=False,
        attrs={
            'td': {'class': 'text-center'}
        }
    )

    full_name = tables.Column(
        linkify=('tenants:tenant_detail', [A('pk')]),
        accessor='get_full_name',
        verbose_name='Name',
        order_by=('first_name', 'last_name')
    )

    email = tables.Column(verbose_name='Email')
    phone = tables.Column(verbose_name='Phone')

    current_property = tables.Column(
        verbose_name='Current Property',
        orderable=False,
        accessor='current_lease.unit.property.property_name'
    )

    current_unit = tables.Column(
        verbose_name='Current Unit',
        orderable=False,
        accessor='current_lease.unit.unit_number'
    )

    current_rent = tables.Column(
        verbose_name='Monthly Rent',
        orderable=False,
        accessor='current_lease.monthly_rent'
    )

    balance = tables.Column(
        verbose_name='Balance',
        orderable=False,
        accessor='current_lease.get_balance'
    )

    status = tables.Column(verbose_name='Status')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.counter = 0

    def render_sno(self, record):
        """Render serial number based on row counter"""
        self.counter += 1
        if hasattr(self, 'page') and self.page:
            return self.page.start_index() + self.counter - 1
        return self.counter

    def render_expand(self, record):
        """Render expand button if tenant has leases"""
        if record.leases.exists():
            return mark_safe(
                f'<button class="btn btn-sm btn-outline-primary toggle-lease" '
                f'data-tenant-id="{record.pk}" title="Show leases">'
                f'<i class="fas fa-chevron-down"></i></button>'
            )
        return ""

    def render_current_property(self, value, record):
        """Render property with link"""
        if record.current_lease and record.current_lease.unit:
            url = reverse('properties:property_detail', args=[
                          record.current_lease.unit.property.id])
            return format_html('<a href="{}">{}</a>', url, value)
        return "-"

    def render_current_unit(self, value, record):
        """Render unit with link"""
        if record.current_lease and record.current_lease.unit:
            url = reverse('properties:unit_detail', args=[
                          record.current_lease.unit.id])
            return format_html('<a href="{}">{}</a>', url, value)
        return "-"

    def render_current_rent(self, value):
        """Format rent value"""
        return f"${float(value):,.2f}" if value else "-"

    def render_balance(self, value):
        """Format balance value"""
        return f"${float(value):,.2f}" if value else "0.00"

    def render_status(self, value, record):
        """Render status with badge"""
        if value == 'active':
            return mark_safe('<span class="badge bg-success">Active</span>')
        return mark_safe('<span class="badge bg-secondary">Inactive</span>')

    def render_actions(self, record):
        active_lease = None
        if hasattr(record, 'active_leases') and record.active_leases:
            # Get the first active lease
            active_lease = record.active_leases[0]

        context = {
            'record': record,
            'view_url': reverse('tenants:tenant_detail', args=[record.pk]),
            'edit_url': reverse('tenants:tenant_update', args=[record.pk]),
            'delete_url': reverse('tenants:tenant_delete', args=[record.pk]),
            'make_payment_url': reverse('payments:payment_create') + f'?lease={active_lease.pk}' if active_lease else None,
            'send_message_url': reverse('notifications:create') + f'?tenant_id={record.pk}',
            'view_ledger_url': reverse('tenants:lease_ledger', kwargs={'lease_id': record.pk}),
            'send_ledger_url': reverse('tenants:send_ledger', kwargs={'pk': record.pk}),
            'whatsapp_url': f"javascript:sendWhatsApp({record.pk})",
            'record': record,  # Pass the entire record for template access
            'sms_url': f"javascript:sendSMS({record.pk}, '{record.phone}', '{record.first_name}', '{active_lease.unit.property.property_name if active_lease else ''}', '{active_lease.unit.unit_number if active_lease else ''}', {active_lease.get_balance if active_lease else 0})",
        }

        return render_to_string('components/action_buttons.html', context)

    class Meta(ExportableTable.Meta):
        model = Tenant
        fields = (
            'sno', 'expand', 'full_name', 'email', 'phone',
            'current_property', 'current_unit', 'current_rent',
            'balance', 'status', 'actions'
        )
        sequence = fields
        attrs = {
            'class': 'table table-striped table-hover table-bordered',
            'thead': {
                'class': 'thead-light'  # Light gray header
            }
        }
        row_attrs = {
            'class': lambda record: 'table-success' if record.is_active else 'table-secondary',
            'data-tenant-id': lambda record: record.pk
        }
        export_formats = ['csv', 'xlsx', 'pdf']  # Add supported export formats


class LedgerTable(tables.Table):
    line_number = tables.Column(
        verbose_name='#', orderable=False, empty_values=())
    date = tables.Column(verbose_name='Date', accessor='transaction_date')
    type = tables.Column(verbose_name='Type')
    description = tables.Column(verbose_name='Description')
    amount = tables.Column(verbose_name='Amount')
    balance = tables.Column(verbose_name='Balance', empty_values=())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.running_balance = Decimal('0.00')

    def render_line_number(self, value, record):
        return self.page.start_index() + record['index'] if hasattr(self, 'page') else record['index'] + 1

    def render_amount(self, value):
        numeric_value = Decimal(str(value))
        formatted_value = "{:,.2f}".format(numeric_value)
        if numeric_value > 0:
            return format_html('<span class="text-success">${}</span>', formatted_value)
        return format_html('<span class="text-danger">-${}</span>', formatted_value)

    def render_balance(self, value, record):
        # Calculate running balance
        self.running_balance += Decimal(str(record['amount']))
        formatted_value = "{:,.2f}".format(self.running_balance)
        if self.running_balance >= 0:
            return format_html('<span class="text-success">${}</span>', formatted_value)
        return format_html('<span class="text-danger">-${}</span>', formatted_value)

    class Meta:
        attrs = {'class': 'table table-striped table-bordered'}
        export_formats = ['csv', 'xlsx', 'pdf']
        sequence = ('line_number', 'date', 'type',
                    'description', 'amount', 'balance')

import django_tables2 as tables
from .models import Payment
from invoices.models import Invoice
from django_tables2.utils import A
from django.utils.html import format_html

from django.urls import reverse, NoReverseMatch
from django.template.loader import render_to_string
from leases.models import Lease
from django.db.models import Sum


def get_lease_url(record, view_name):
    try:
        return reverse(f'leases:{view_name}', args=[record.lease.pk])
    except NoReverseMatch:
        return '#'


class PaymentTable1(tables.Table):
    # Serial number column (automatic counter)
    sn = tables.Column(
        verbose_name='S.N #',
        empty_values=(),
        orderable=False,
        attrs={"td": {"class": "text-left"}}
    )

    id = tables.LinkColumn(
        'payments:payment_detail',
        args=[tables.A('pk')],
        verbose_name='Payment ID'
    )

    # Tenant name (clickable - links to tenant detail)
    tenant = tables.Column(
        verbose_name='Tenant',
        accessor='lease.tenant',
        order_by=('lease.tenant.first_name', 'lease.tenant.last_name'),
        linkify=lambda record: reverse(
            'tenants:tenant_detail',
            args=[record.lease.tenant.pk]
        )
    )

    # Unit + Property (clickable - links to lease detail)
    unit_property = tables.Column(
        verbose_name='Property',
        accessor='lease.unit',
        order_by='lease.unit.property.property_name',
        linkify=lambda record: reverse(
            'leases:lease_detail',
            args=[record.lease.pk]
        )
    )

    # Amount (clickable - links to lease ledger)
    amount = tables.Column(
        verbose_name='Amount',
        linkify=lambda record: reverse(
            'leases:lease_ledger_by_pk',
            args=[record.lease.pk]
        )
    )

    payment_method = tables.Column(verbose_name='Method')
    payment_date = tables.DateColumn(verbose_name='Date')

    balance = tables.Column(
        accessor='lease.get_balance',
        verbose_name='Balance',
        orderable=False,
        attrs={
            "td": {"class": "text-end"},
            "th": {"class": "text-end"}
        }
    )
    actions = tables.TemplateColumn(
        template_name='components/action_buttons.html',
        verbose_name='Actions',
        orderable=False,

        attrs={"td": {"class": "text-nowrap"}}
    )

    # Custom rendering methods
    def render_balance(self, value):
        """Format balance for exports"""
        return f"{float(value):,.2f}" if value else "0.00"

    def render_sn(self):
        """Auto-incrementing serial number"""
        self.row_counter = getattr(self, 'row_counter', 0) + 1
        return self.row_counter

    def render_tenant(self, value, record):
        return f"{record.lease.tenant.first_name} {record.lease.tenant.last_name}"

    def render_unit_property(self, value, record):
        unit = record.lease.unit
        return f"{unit.property.property_name} - {unit.unit_number}"

    def render_amount(self, value):
        return f"{value:,.2f}"

    def render_payment_date(self, value):
        return value.strftime('%Y-%m-%d') if value else ''

    def render_actions(self, record):
        return render_to_string('components/action_buttons.html', {
            'view_url': reverse('payments:payment_detail', args=[record.pk]),
            'edit_url': reverse('payments:payment_update', args=[record.pk]),
            'delete_url': reverse('payments:payment_delete', args=[record.pk]),
            'receipt_url': reverse('payments:send_receipt', args=[record.pk]),

        })

    def get_total_payment(self):
        """Calculate total payment amount"""
        return self.data.aggregate(total=Sum('amount'))['total'] or 0

    def render_footer(self):
        """Render footer with total payment"""
        total = self.get_total_payment()
        return format_html(
            '<tr class="table-active">'
            '<td colspan="4" class="text-end fw-bold">Total Payments:</td>'
            '<td class="text-end fw-bold">{}</td>'
            '<td colspan="3"></td>'
            '</tr>',
            f"{float(total):,.2f}"
        )

    pdf_export_attrs = {
        'orientation': 'portrait',
        'column_widths': {
            'sn': 20,
            'id': 20,
            'tenant': 100,
            'unit_property': 140,
            'payment_date': 60,
            'amount': 60,
            'payment_method': 60,
            'balance': 60,

        },
        'pdf_export_title': 'Payments Report',
        'pdf_footer': lambda table: f"Total Payments: {float(table.get_total_payment()):,.2f}"
    }

    class Meta:
        model = Payment
        template_name = 'django_tables2/bootstrap5.html'
        fields = (
            'sn', 'id', 'tenant', 'unit_property',
            'payment_date', 'amount', 'payment_method', 'balance', 'actions'
        )
        attrs = {
            'class': 'table table-striped table-hover',
            'thead': {'class': 'thead-light'}
        }
        order_by = ('-payment_date',)
        export_formats = ['csv', 'xlsx', 'pdf']  # Add this line

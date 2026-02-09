import django_tables2 as tables
from .models import Payment
from django_tables2.utils import A
from django.utils.html import format_html
from django.urls import reverse
from django.urls import reverse, NoReverseMatch
from django.template.loader import render_to_string
from leases.models import Lease
from django.db.models import Sum
from django.utils.safestring import mark_safe


class PaymentTable(tables.Table):
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

    # Tenant name (properly ordered)
    tenant = tables.Column(
        verbose_name='Tenant',
        accessor='lease.tenant',
        order_by=('lease__tenant__first_name', 'lease__tenant__last_name'),
        linkify=lambda record: reverse(
            'tenants:tenant_detail',
            args=[record.lease.tenant.pk]
        )
    )

    # Property and Unit (properly ordered)
    property = tables.Column(
        verbose_name='Property',
        accessor='lease.unit.property.property_name',
        order_by='lease__unit__property__property_name'
    )

    unit = tables.Column(
        verbose_name='Unit',
        accessor='lease.unit.unit_number',
        order_by='lease__unit__unit_number'
    )

    payment_date = tables.DateColumn(verbose_name='Date')
    amount = tables.Column(verbose_name='Amount')
    payment_method = tables.Column(verbose_name='Method')

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
    def render_tenant(self, value, record):
        return f"{record.lease.tenant.first_name} {record.lease.tenant.last_name}"

    def render_amount(self, value):
        return f"{float(value):,.2f}"

    def render_balance(self, value):
        return f"{float(value):,.2f}" if value else "0.00"

    def render_sn(self):
        """Auto-incrementing serial number"""
        self.row_counter = getattr(self, 'row_counter', 0) + 1
        return self.row_counter

    def render_actions(self, record):
        return render_to_string('components/action_buttons.html', {
            'view_url': reverse('payments:payment_detail', args=[record.pk]),
            'edit_url': reverse('payments:payment_update', args=[record.pk]),
            'delete_url': reverse('payments:payment_delete', args=[record.pk]),
            'receipt_url': reverse('payments:send_receipt', args=[record.pk]),
        })

    def get_total_payment(self):
        """Calculate total payment amount"""
        return sum(payment.amount for payment in self.data)

    def render_footer(self):
        """Render footer with total payment"""
        total = sum(payment.amount for payment in self.data)
        return format_html(
            '<tr class="table-active">'
            '<td colspan="5" class="text-end fw-bold">Total Payments:</td>'
            '<td class="text-end fw-bold">{}</td>'
            '<td colspan="2"></td>'
            '</tr>',
            f"{float(total):,.2f}"
        )

    class Meta:
        model = Payment
        template_name = 'django_tables2/bootstrap5-responsive.html'
        fields = (
            'sn', 'id', 'tenant', 'property', 'unit',
            'payment_date', 'amount', 'payment_method', 'balance', 'actions'
        )
        attrs = {
            'class': 'table table-striped table-hover',
            'thead': {'class': 'thead-light'},
            'tfoot': {'class': 'table-active'}
        }
        order_by = ('-payment_date',)
        export_formats = ['csv', 'xlsx', 'pdf']
        sequence = (
            'sn', 'id', 'tenant', 'property', 'unit',
            'payment_date', 'amount', 'payment_method', 'balance', 'actions'
        )

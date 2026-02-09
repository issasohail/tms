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
from django.utils.html import format_html
from django.urls import reverse

def _truncate(s, n):
        s = (s or "").strip()
        return (s[:n] + "…") if len(s) > n else s
class PaymentTable(tables.Table):
    # Serial number column (automatic counter)
    sn = tables.Column(
        verbose_name='S.N #',
        empty_values=(),
        orderable=False,
        attrs={"td": {"class": "text-center col-sn"}}
    )

    id = tables.LinkColumn(
        'payments:payment_detail',
        args=[tables.A('pk')],
        verbose_name='ID',
        attrs={"td": {"class": "col-id"}, "th": {"class": "col-id"}}


    )

    # Tenant name (properly ordered)
    tenant = tables.Column(
        verbose_name='Tenant',
        accessor='lease.tenant',
        order_by=('lease__tenant__first_name', 'lease__tenant__last_name'),
        linkify=lambda record: reverse(
            'tenants:tenant_detail',
            args=[record.lease.tenant.pk]
        ),
        attrs={"td": {"class": "col-tenant"}, "th": {"class": "col-tenant"}}
    )

    # Property and Unit (properly ordered)
    property = tables.Column(
        verbose_name='Property',
        accessor='lease.unit.property.property_name',
        order_by='lease__unit__property__property_name',
        attrs={"td": {"class": "col-property"},
               "th": {"class": "col-property"}}
    )

    unit = tables.Column(
        verbose_name='Unit',
        accessor='lease.unit.unit_number',
        order_by='lease__unit__unit_number',
        attrs={"td": {"class": "col-unit"}, "th": {"class": "col-unit"}}
    )

    payment_date = tables.DateColumn(
        verbose_name='Date',
        attrs={"td": {"class": "col-date"}, "th": {"class": "col-date"}}
    )

    amount = tables.Column(
        verbose_name='Amount',
        attrs={"td": {"class": "col-amount"}, "th": {"class": "col-amount"}}
    )

    payment_method = tables.Column(
        verbose_name='Method',
        attrs={"td": {"class": "col-method"}, "th": {"class": "col-method"}}
    )
    description = tables.Column(verbose_name="Desc", empty_values=(), orderable=False)

    balance = tables.Column(
        accessor='lease.get_balance',
        verbose_name='Balance',
        orderable=False,
        attrs={"td": {"class": "text-center col-balance"},
               "th": {"class": "text-center col-balance"}}
    )

    actions = tables.TemplateColumn(
        template_name='components/action_buttons.html',
        verbose_name='Actions',
        orderable=False,
        attrs={"td": {"class": "text-nowrap col-actions actions-cell"},
               "th": {"class": "col-actions actions-cell"}}
    )

    # Custom rendering methods
    def render_tenant(self, value, record):
        full = f"{record.lease.tenant.first_name} {record.lease.tenant.last_name}"
        short = (full[:15] + "…") if len(full) > 15 else full  # ✅ 15-char cap
        url = reverse('tenants:tenant_detail', args=[record.lease.tenant.pk])
        return format_html(
            '<a href="{}" class="text-decoration-none">'
            '<span class="truncate-15" title="{}">{}</span>'
            '</a>',
            url,
            full,
            short
        )

    def render_amount(self, value):
        return f"Rs. {float(value):,.2f}"

    def render_balance(self, value):
        return f"Rs. {float(value):,.2f}" if value else "0.00"

    def render_sn(self):
        """Auto-incrementing serial number"""
        self.row_counter = getattr(self, 'row_counter', 0) + 1
        return self.row_counter

    def render_actions(self, record):
        return render_to_string('components/action_buttons.html', {
            'view_url': reverse('payments:allocation_detail', args=[record.pk]),
            'edit_url': reverse('payments:payment_update', args=[record.pk]),
            'delete_url': reverse('payments:payment_delete', args=[record.pk]),
            'make_payment_url': reverse('payments:payment_create') + f'?lease={record.lease.pk}',
            'whatsapp_url': f"javascript:sendWhatsApp({record.pk})",
            'record': record,  # Pass the entire record for template access
            'is_payment_row': True,
        })

    def get_total_payment(self):
        """Calculate total payment amount"""
        return "test"+sum(payment.amount for payment in self.data)
    


    def render_payment_method(self, value, record):
        full = str(value or "")
        return format_html('<span title="{}">{}</span>', full, _truncate(full, 12))


    def render_description(self, value, record):
        # Decide where description comes from
        # If your CashLedgerRow carries it:
        full = str(getattr(record, "description", "") or "")
        short = _truncate(full, 12)
        return format_html('<span title="{}">{}</span>', full, short)


    def render_property(self, value, record):
        # value might already be property name depending on accessor
        full = str(value or "")
        short = _truncate(full, 8)
        return format_html('<span title="{}">{}</span>', full, short)

    def before_render(self, request):
        """Set export title based on filters before rendering"""
        if request and hasattr(request, 'GET'):
            filters = request.GET
            title_parts = ["Payment Records"]

            # Add filter-specific parts to title
            if filters.get('tenant'):
                try:
                    from tenants.models import Tenant
                    tenant = Tenant.objects.get(pk=filters['tenant'])
                    title_parts.append(f"for {tenant.get_full_name()}")
                except:
                    pass

            if filters.get('property'):
                try:
                    from properties.models import Property
                    prop = Property.objects.get(pk=filters['property'])
                    title_parts.append(f"at {prop.property_name}")
                except:
                    pass

            if filters.get('unit'):
                try:
                    from properties.models import Unit
                    unit = Unit.objects.get(pk=filters['unit'])
                    title_parts.append(f"(Unit {unit.unit_number})")
                except:
                    pass

            if filters.get('date_range') or (filters.get('start_date') and filters.get('end_date')):
                if filters.get('date_range'):
                    date_range = filters['date_range'].replace(
                        '_', ' ').title()
                    title_parts.append(f"for {date_range}")
                else:
                    title_parts.append(
                        f"from {filters['start_date']} to {filters['end_date']}")

            self.export_title = " ".join(title_parts)

    pdf_export_attrs = {
        'orientation': 'portrait',
        'column_widths': {
            'sn': 20,
            'id': 20,
            'tenant': 100,
            'property': 60,
            'unt': 60,
            'unit_property': 140,
            'payment_date': 60,
            'amount': 60,
            'payment_method': 60,
            'description':20,
            'balance': 60,

        },
        'pdf_export_title': 'Payments Report',

    }

    class Meta:
        model = Payment
        template_name = 'django_tables2/bootstrap5-responsive.html'
        fields = (
            'sn', 'id', 'tenant', 'property', 'unit',
            'payment_date', 'amount', 'payment_method', 'description','balance', 'actions'
        )
        attrs = {
            'class': 'table table-sm table-bordered table-hover align-middle',
            'thead': {'class': 'table-light'}
        }
        order_by = ('-payment_date', '-id')
        export_formats = ['csv', 'xlsx', 'pdf']
        sequence = (
            'sn', 'id', 'tenant', 'property', 'unit',
            'payment_date', 'amount', 'payment_method', 'balance', 'actions'
        )

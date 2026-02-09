# leases/tables.py
from utils.pdf_export import handle_export
from django.db.models import Sum
from django_tables2 import SingleTableView
from .models import Lease
from decimal import Decimal
from django_tables2 import tables
from django.urls import reverse
from django.template.loader import render_to_string
from properties.tables import ExportableTable
from django_tables2.columns import DateColumn
from django.utils.safestring import mark_safe
from django.utils import timezone
from django.utils.html import escape
from invoices.services import security_deposit_balance

from django.utils.html import format_html
from decimal import Decimal
import json
from leases.utils.billing import security_deposit_balance  # adjust path if different


class LeaseTable(ExportableTable):

    tenant = tables.Column(
        accessor='tenant',
        order_by=('tenant__first_name', 'tenant__last_name'),
        verbose_name='Tenant',
        linkify=lambda record: reverse(
            'tenants:tenant_detail',  args=[record.tenant.pk]),
        attrs={"td": {"class": "col-tenant"}, "th": {"class": "col-tenant"}}

    )

    property = tables.Column(
        accessor='unit.property.property_name',
        verbose_name='Property',
        linkify=lambda record: reverse('properties:property_detail', args=[
                                       record.unit.property.pk]),
        attrs={"td": {"class": "col-property"},
               "th": {"class": "col-property"}}  # keep
    )

    unit = tables.Column(
        accessor='unit.unit_number',
        verbose_name='Unit',
        linkify=lambda record: reverse(
            'properties:unit_detail', args=[record.unit.pk]),
        attrs={"td": {"class": "col-unit"},
               "th": {"class": "col-unit"}}  # ✨ change to col-unit
    )

    status = tables.Column(attrs={
        "td": {"class": "col-status"},
        "th": {"class": "col-status"}
    })

    start_date = DateColumn(
        format="M d, Y", verbose_name='Start Date',
        attrs={"td": {"class": "col-start"},
               "th": {"class": "col-start"}}  # ✨ add
    )
    end_date = DateColumn(
        format="M d, Y", verbose_name='End Date',
        attrs={"td": {"class": "col-end"}, "th": {"class": "col-end"}}  # ✨ add
    )

    balance = tables.Column(
        accessor='get_balance',
        verbose_name='Balance',
        linkify=lambda record: reverse(
            'leases:lease_ledger_by_pk', args=[record.pk]),
        attrs={"td": {"class": "col-balance text-center"},
               "th": {"class": "col-balance text-end"}},
        orderable=False
    )

    security_due = tables.Column(
        accessor='security_due',
        verbose_name='Sec. Due',
        attrs={
            "td": {"class": "col-sec text-end"},
            "th": {"class": "col-sec text-end"}
        },
        orderable=False,
    )

    monthly_payments = tables.Column(
        accessor='get_monthly_payment',
        verbose_name=mark_safe('Monthly\nPayment'),
        attrs={"td": {"class": "col-monthly text-end"},
               "th": {"class": "col-monthly monthly-col"}}  # ✨ add td class
    )

    actions = tables.Column(verbose_name='Actions', orderable=False,
                            empty_values=(),
                            attrs={"td": {"class": "col-actions actions-cell"}, "th": {"class": "col-actions actions-cell"}})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Attach classes to the auto-added 'sn' column so we can style it
        if 'sn' in self.columns:
            bc = self.columns['sn']           # BoundColumn
            col = bc.column                   # TemplateColumn
            # <-- use verbose_name (header has no setter)
            col.verbose_name = 'SN#'

            # merge attrs so we don't clobber anything already set
            existing = col.attrs or {}
            th = (existing.get('th') or {}).copy()
            td = (existing.get('td') or {}).copy()
            th['class'] = (th.get('class', '') + ' col-sn').strip()
            td['class'] = (td.get('class', '') + ' col-sn').strip()
            col.attrs = {**existing, 'th': th, 'td': td}

    def render_monthly_payments(self, value):
        """Render total payments using the model's monthly_payments property"""
        # payments = record.total_payments
        return f"Rs. {int(value):,}" if value else "0.00"

    def render_balance(self, value, record):
        """Format balance for display and exports"""
        formatted_value = f"Rs. {int(value):,}" if value else "0.00"

        # For exports, just return the formatted value
        if hasattr(self, 'export_formats') and getattr(self, 'is_export', False):
            return formatted_value

        # For HTML display, return the formatted value (it will be automatically linked)
        return formatted_value
    
    def render_security_due(self, value, record):
        formatted = f"Rs. {int(value):,}" if value else "0.00"

        # Exports (CSV/XLSX/PDF)
        if hasattr(self, 'export_formats') and getattr(self, 'is_export', False):
            return formatted

        # HTML: highlight if > 0
        if value and value > 0:
            return mark_safe(f'<span class="text-danger fw-bold">{formatted}</span>')
        return formatted

    def render_start_date(self, value):
        """Format date for exports"""
        return value.strftime('%Y-%m-%d') if value else ""

    def render_end_date(self, value, record):
        """Format date for display and add styling if ending soon"""
        formatted_date = value.strftime('%Y-%m-%d') if value else ""

        # For exports, just return the formatted date
        # For exports (CSV, Excel), return simple formatted date
        request = getattr(self, 'request', None)
        if request and request.GET.get('_export'):
            return value.strftime('%Y-%m-%d')

        if hasattr(self, 'export_formats') and getattr(self, 'is_export', False):
            return value.strftime('%Y-%m-%d')

        # For HTML display, check if ending within 40 days
        if value:
            remaining_days = (value - timezone.now().date()).days
            if 0 <= remaining_days <= 40:
                return mark_safe(f'<span class="ending-soon">{value.strftime("%b %d, %Y")}</span>')

        return value.strftime("%b %d, %Y") if value else ""

    def render_end_date1(self, value):
        """Format date for exports"""
        return value.strftime('%Y-%m-%d') if value else ""

    def render_property(self, value, record):
        full = value or ""
        short = (full[:8] + "…") if len(full) > 8 else full
        url = reverse('properties:property_detail',
                      args=[record.unit.property.pk])
        return mark_safe(f'<a href="{url}" title="{escape(full)}">{escape(short)}</a>')

    def render_tenant(self, record, value):
        t = record.tenant
        full = f"{t.first_name} {t.last_name}".strip() if t else ""
        return mark_safe(f'<span class="tenant-text">{full}</span>')

    def render_actions(self, record):
        # Monthly / normal balance (rent, maintenance, etc.)
        base_balance = record.get_balance or Decimal('0.00')

        # Security deposit balance (to collect)
        sec_balance_dec = security_deposit_balance(record)

        # Combined total due
        total_due = base_balance + sec_balance_dec

        has_balance = total_due > Decimal('0.00')
        tenant_phone = record.tenant.phone or ''

        whatsapp_url = None
        if has_balance:
            # --- Period (begin / end or Ongoing) ---
            start = record.start_date
            end = record.end_date
            period_start = start.strftime("%b %d, %Y") if start else ""
            period_end = end.strftime("%b %d, %Y") if end else "Ongoing"

            # --- Security deposit required (agreed) ---
            # You can use required from SDT, but simplest is lease.security_deposit for display
            sec_required_dec = record.security_deposit or Decimal("0.00")
            security_required = f"Rs. {sec_required_dec:,.2f}"

            # --- Security deposit balance & status ---
            security_balance = f"Rs. {sec_balance_dec:,.2f}" if sec_balance_dec > 0 else ""
            security_status = "Pending" if sec_balance_dec > 0 else "Paid"

            whatsapp_url = (
                "sendWhatsAppReminder("
                f"{json.dumps(tenant_phone)}, "
                f"{json.dumps(record.tenant.first_name)}, "
                f"{json.dumps(record.unit.property.property_name)}, "
                f"{json.dumps(record.unit.unit_number)}, "
                f"{float(total_due)}, "            # 👈 now passing total (monthly + security)
                f"{json.dumps(period_start)}, "
                f"{json.dumps(period_end)}, "
                f"{json.dumps(security_required)}, "
                f"{json.dumps(security_status)}, "
                f"{json.dumps(security_balance)}"
                ")"
            )

        return render_to_string('components/action_buttons.html', {
            'record': record,
            'view_url': reverse('leases:lease_detail', args=[record.pk]),
            'edit_url': reverse('leases:lease_update', args=[record.pk]),
            'delete_url': reverse('leases:lease_delete', args=[record.pk]),
            'make_payment_url': reverse('payments:payment_create') + f'?lease={record.pk}',
            'whatsapp_url': whatsapp_url,
            'has_balance': has_balance,
            'is_lease_row': True,
        })


    class Meta(ExportableTable.Meta):
        model = Lease
        fields = (
            'sn', 'id', 'tenant', 'property', 'unit', 'monthly_payments',
            'status', 'start_date', 'end_date', 'balance','security_due', 'actions'
        )
        sequence = fields
        order_by = ('unit',)
        export_formats = ['csv', 'xlsx', 'pdf']  # Add supported export formats
class LeaseListView(SingleTableView):
    model = Lease
    table_class = LeaseTable
    template_name = 'leases/lease_list.html'

    def get_queryset(self):
        queryset = super().get_queryset()

        # Annotate with total payments
        queryset = queryset.annotate(
            total_payments=Sum('payments__amount')
        )

        # Filter for non-zero balance if requested
        if self.request.GET.get('nonzero_balance') == 'on':
            queryset = queryset.annotate(
                calculated_balance=F('monthly_rent') - F('total_payments')
            ).filter(calculated_balance__gt=0)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['nonzero_balance'] = self.request.GET.get(
            'nonzero_balance', '')
        return context

    def get(self, request, *args, **kwargs):
        resp = handle_export(request, self.get_table(), 'leases')
        return resp or super().get(request, *args, **kwargs)


# --- TENANT DETAIL LEASE TABLE -----------------------


class TenantLeaseTable(ExportableTable):
    # NOTE: ExportableTable in your project already supports 'sn' numbering,
    # since LeaseTable uses it in Meta.fields. We’ll include 'sn' here too. :contentReference[oaicite:0]{index=0}

    property = tables.Column(
        accessor='unit.property.property_name',
        verbose_name='Property',
        linkify=lambda record: reverse('properties:property_detail', args=[
                                       record.unit.property.pk]),
        attrs={"td": {"class": "col-property"}}
    )

    unit = tables.Column(
        accessor='unit.unit_number',
        verbose_name='Unit',
        linkify=lambda record: reverse(
            'properties:unit_detail', args=[record.unit.pk]),
        attrs={"td": {"class": "col-unit-wide"}}
    )

    status = tables.Column(
        verbose_name='Status',
        attrs={"td": {"class": "text-center"}, "th": {"class": "text-center"}}
    )

    start_date = DateColumn(
        format="Y-m-d", verbose_name='Start Date', attrs={"td": {"class": "col-date"}})
    end_date = DateColumn(format="Y-m-d", verbose_name='End Date',
                          attrs={"td": {"class": "col-date"}})

    monthly_payment = tables.Column(
        # you already expose this on Lease; your LeaseTable uses it too :contentReference[oaicite:1]{index=1}
        accessor='get_monthly_payment',
        verbose_name='Monthly Payment',
        attrs={"td": {"class": "text-end"},
               "th": {"class": "text-center monthly-col"}}
    )

    balance = tables.Column(
        accessor='get_balance',
        verbose_name='Balance',
        attrs={"td": {"class": "text-end"}, "th": {"class": "text-end"}},
        orderable=False
    )

    actions = tables.Column(
        verbose_name='Actions', orderable=False,
        attrs={"td": {"class": "col-actions"}, "th": {"class": "col-actions"}}
    )

    # ---- renderers ----
    def render_monthly_payment(self, value):
        return f"{int(value):,}" if value else "0.00"

    def render_balance(self, value):
        return f"{int(value):,}" if value else "0.00"

    def render_end_date(self, value):
        return value.strftime('%Y-%m-%d') if value else ""

    def render_start_date(self, value):
        return value.strftime('%Y-%m-%d') if value else ""

    def render_action(self, record):
        url = reverse('leases:lease_detail', args=[record.pk])
        return mark_safe(f'<a class="btn btn-sm btn-secondary" href="{url}">Detail</a>')

    class Meta(ExportableTable.Meta):
        model = Lease
        # Include 'sn' for S.No like your LeaseTable does
        fields = ('sn', 'property', 'unit', 'monthly_payment',
                  'status', 'start_date', 'end_date', 'balance', 'action')
        sequence = fields
        attrs = {
            "class": "table table-sm table-striped table-hover align-middle",
            "thead": {"class": "table-light"},
            "th": {"class": "text-nowrap"},
            # keep one line per row (no wrapping)
            "td": {"class": "text-nowrap"},
        }

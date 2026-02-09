# invoices/tables.py
from django.utils.html import format_html
from django.urls import reverse
from django.template.loader import render_to_string
import django_tables2 as tables
from django_tables2.columns import DateColumn, Column
from .models import Invoice
from properties.tables import ExportableTable
from utils.pdf_export import handle_export
from django.utils.html import format_html
from django.utils.safestring import mark_safe


# invoices/tables.py
from django.urls import reverse
from django.utils.safestring import mark_safe
import django_tables2 as tables
from django_tables2.columns import DateColumn, Column
from .models import Invoice
from properties.tables import ExportableTable


class InvoiceTable(ExportableTable):

    sn = tables.Column(
        verbose_name='S.N #',
        empty_values=(),
        orderable=False,
        attrs={"td": {"class": "text-center col-sn"}}
    )

    select = tables.CheckBoxColumn(accessor="pk", orderable=False)

    invoice_number = tables.Column(
        verbose_name="Serial#",
        accessor="invoice_number",
        orderable=True,

        linkify=lambda record: reverse(
            "invoices:invoice_detail", args=[record.pk]),
        attrs={"th": {"class": "col-invno"},
               "td": {"class": "text-nowrap col-invno"}},
    )

    # ✅ Sortable: first name + last name via explicit order_by tuple
    tenant = Column(
        accessor="lease__tenant__first_name",  # real path to first name
        verbose_name="Tenant",
        order_by=("lease__tenant__first_name", "lease__tenant__last_name"),
        orderable=True,
        attrs={"td": {"class": "text-nowrap tenant-cell"}},  # one line


    )

    # ✅ Sortable by property then unit via order_by list
    property_unit = Column(
        empty_values=(),
        verbose_name="Property",
        order_by=("lease__unit__property__property_name",
                  "lease__unit__unit_number"),
        orderable=True,
        attrs={"th": {"class": "col-propunit"},
               "td": {"class": "col-propunit"}},
    )

    description = Column(accessor="description",
                         verbose_name="Description", orderable=True,
                         attrs={"th": {"class": "col-desc"}, "td": {"class": "col-desc"}},)

    # Dates as "Jan 01,2025"
    issue_date = DateColumn(format="M d,Y", verbose_name="Issue Date",
                            attrs={"th": {"class": "col-issue"}, "td": {"class": "col-issue"}},)
    due_date = DateColumn(format="M d,Y", verbose_name="Due Date",
                          attrs={"th": {"class": "col-due"}, "td": {"class": "col-due"}},)

    total_amount = Column(
        accessor="amount", verbose_name="Amount", orderable=True,
        attrs={"th": {"class": "col-amount text-end"},
               "td": {"class": "col-amount text-end"}},
    )

    actions = tables.TemplateColumn(
        template_name='components/action_buttons.html',
        verbose_name='Actions',
        orderable=False,

        attrs={"td": {"class": "text-nowrap actions-cell"}}
    )

    class Meta(ExportableTable.Meta):
        model = Invoice
        fields = (
            "sn", "select",
            "invoice_number",
            "property_unit",
            "tenant",
            "description",
            "issue_date",
            "due_date",
            "total_amount",
            "actions",
        )
        sequence = fields
        # default only (don’t force on every click)
        order_by = ("-issue_date", "property_unit")

    # ---------- renderers ----------
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sn_seen = 0

    def render_sn(self):
        """Auto-incrementing serial number"""
        self.row_counter = getattr(self, 'row_counter', 0) + 1
        return self.row_counter

    def render_tenant(self, record, value):
        first = (value or "").strip()
        last = ""
        try:
            last = (getattr(record.lease.tenant, "last_name", "") or "").strip()
        except Exception:
            pass
        display = (f"{first} {last}".strip() or first)[:15]  # 15 chars
        return display or "—"

    def render_property_unit(self, record):
        try:
            unit = record.lease.unit
            prop = unit.property
            prop_name = (getattr(prop, "property_name", "") or "").strip()
            unit_no = getattr(unit, "unit_number", "") or ""
            if prop_name and unit_no:
                return format_html(
                    '<span title="{}">{}-{}</span>',
                    prop_name, prop_name[:8], unit_no
                )
        except Exception:
            pass
        return "—"

    def render_description(self, value):
        text = (value or "").strip()
        show = (text[:30] + "…") if len(text) > 30 else text
        return mark_safe(f'<span class="d-inline-block text-truncate" style="max-width:30ch" title="{text}">{show}</span>')

    def render_total_amount(self, value):
        return f"Rs. {float(value):,.2f}" if value else "Rs. 0.00"

    def render_actions(self, record):
        return render_to_string('components/action_buttons.html', {
            'record': record,  # <-- IMPORTANT: give the template access to the row
            'view_url': reverse('invoices:invoice_detail', args=[record.pk]),
            'edit_url': reverse('invoices:invoice_update', args=[record.pk]),
            'delete_url': reverse('invoices:invoice_delete', args=[record.pk]),
            # optional
            'make_payment_url': reverse('payments:payment_create') + f'?invoice={record.pk}',
            'whatsapp_url': (
                f"fetchWhatsAppPayload('{reverse('invoices:api_invoice_whatsapp', args=[record.pk])}')"
                ".then(d=>openWhatsApp(d.phone,d.message)).catch(()=>{})"
            )



        })


class InvoiceListView(tables.SingleTableView):
    model = Invoice
    table_class = InvoiceTable
    template_name = 'invoices/invoice_list.html'

    def get(self, request, *args, **kwargs):
        resp = handle_export(request, self.get_table(), 'invoices')
        return resp or super().get(request, *args, **kwargs)

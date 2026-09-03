# invoices/tables.py
from django.utils.html import format_html
from django.urls import reverse
from urllib.parse import urlencode
import json
from django.core.cache import cache
import django_tables2 as tables
from django_tables2.columns import DateColumn, Column
from .models import Invoice
from properties.tables import ExportableTable
from utils.pdf_export import handle_export
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from core.currency import format_money
from core.models import GlobalSettings


# invoices/tables.py
from django.urls import reverse
from django.utils.safestring import mark_safe
import django_tables2 as tables
from django_tables2.columns import DateColumn, Column
from .models import Invoice
from properties.tables import ExportableTable


def _settings_obj():
    settings_obj = cache.get("core.global_settings")
    if settings_obj is None:
        settings_obj = GlobalSettings.get_solo()
        cache.set("core.global_settings", settings_obj, 60)
    return settings_obj


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
        order_by=("historical_property_name", "historical_unit_number"),
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

    lease_balance = Column(
        empty_values=(),
        verbose_name="Lease Balance",
        orderable=False,
        attrs={
            "th": {"class": "col-balance text-end"},
            "td": {"class": "col-balance text-end"},
        },
    )

    status = Column(
        accessor="status",
        verbose_name="Status",
        orderable=True,
        attrs={"th": {"class": "col-status"}, "td": {"class": "col-status"}},
    )

    actions = tables.Column(
        empty_values=(),
        verbose_name='Actions',
        orderable=False,
        attrs={"td": {"class": "text-nowrap actions-cell"}},
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
            "lease_balance",
            "status",
            "actions",
        )
        sequence = fields
        # default only (don’t force on every click)
        order_by = ("-issue_date", "property_unit")

    # ---------- renderers ----------
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.row_counter = 0

    def render_sn(self):
        """Auto-incrementing serial number across paginated pages."""
        self.row_counter += 1
        page = getattr(self, "page", None)
        offset = page.start_index() - 1 if page is not None else 0
        return offset + self.row_counter

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
            unit = record.historical_unit
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
        return format_money(value, _settings_obj())

    def render_lease_balance(self, record):
        balance = getattr(record, "dashboard_lease_balance", None)
        if balance is None:
            try:
                balance = record.lease.get_balance()
            except Exception:
                balance = None
        if balance is None:
            return "-"
        return format_money(balance, _settings_obj())

    def render_status(self, record, value):
        lifecycle = getattr(record, "lifecycle_status", "issued") or "issued"
        lifecycle_label = record.get_lifecycle_status_display() if hasattr(record, "get_lifecycle_status_display") else lifecycle
        payment_status = getattr(record, "dashboard_payment_status", None) or record.payment_status
        payment_label = {
            "paid": "Paid", "partially_paid": "Partially Paid", "unpaid": "Unpaid",
            "overdue": "Overdue", "overpaid": "Overpaid",
        }.get(payment_status, payment_status.replace("_", " ").title())
        lifecycle_class = {
            "issued": "primary", "draft": "secondary", "disputed": "warning text-dark",
            "cancelled": "danger", "void": "dark", "written_off": "secondary",
        }.get(lifecycle, "secondary")
        payment_class = {
            "paid": "success", "overpaid": "info text-dark", "partially_paid": "warning text-dark",
            "overdue": "danger", "unpaid": "danger",
        }.get(payment_status, "secondary")
        return format_html(
            '<span class="invoice-status-stack" title="Payment status updates automatically from payments and due date. Lifecycle status can be changed manually from invoice detail.">'
            '<span class="badge bg-{} invoice-status-badge invoice-status-lifecycle"><i class="fas fa-file-invoice me-1"></i>{}</span>'
            '<span class="badge bg-{} invoice-status-badge invoice-status-payment"><i class="fas fa-wallet me-1"></i>{}</span>'
            '</span>',
            lifecycle_class, lifecycle_label, payment_class, payment_label,
        )

    def render_actions(self, record):
        delete_url = reverse("invoices:invoice_delete", args=[record.pk])
        request = getattr(self, "request", None)
        if request is not None:
            delete_url = f"{delete_url}?{urlencode({'return_to': request.get_full_path()})}"

        view_url = reverse("invoices:invoice_detail", args=[record.pk])
        edit_url = reverse("invoices:invoice_update", args=[record.pk])
        pay_url = reverse("payments:payment_create") + f"?invoice={record.pk}"

        lease = getattr(record, "lease", None)
        tenant = getattr(lease, "tenant", None) if lease else None
        unit = getattr(record, "historical_unit", None)
        prop = getattr(unit, "property", None) if unit else None
        phone = getattr(tenant, "phone", "") or ""

        items_payload = json.dumps(
            [
                {
                    "cat": getattr(getattr(item, "category", None), "name", "") or "",
                    "desc": getattr(item, "description", "") or "",
                    "amount": f"{(getattr(item, 'amount', None) or 0):,.2f}",
                }
                for item in record.items.all()
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        lease_balance = getattr(record, "dashboard_lease_balance", None)
        if lease_balance is None and lease is not None:
            lease_balance = lease.get_balance
        security_balance = getattr(record, "dashboard_security_balance", None)
        if security_balance is None and lease is not None:
            security_balance = lease.security_balance_to_collect
        total_balance = getattr(record, "dashboard_total_balance", None)

        if phone:
            whatsapp = format_html(
                '<a href="#" class="btn btn-sm btn-success action-btn btn-wa-invoice" '
                'title="Send WhatsApp" onclick="return window.sendWhatsAppSingleInvoiceFromList(this);" '
                'data-phone="{}" data-object-id="{}" data-fname="{}" data-lname="{}" '
                'data-invno="{}" data-issue="{}" data-issue-month="{}" data-due="{}" '
                'data-property="{}" data-unit="{}" data-status="{}" data-leasebal="{}" '
                'data-securitybal="{}" data-totalbal="{}" data-total="{}" data-items="{}">'
                '<i class="fab fa-whatsapp"></i><span class="btn-text ms-1">WhatsApp</span></a>',
                phone, record.pk, getattr(tenant, "first_name", "") or "",
                getattr(tenant, "last_name", "") or "", record.invoice_number or "",
                record.issue_date.strftime("%b %d, %Y") if record.issue_date else "",
                record.issue_date.strftime("%Y-%m") if record.issue_date else "",
                record.due_date.strftime("%b %d, %Y") if record.due_date else "",
                getattr(prop, "property_name", "") or "",
                getattr(unit, "unit_number", "") or "",
                record.get_status_display() if hasattr(record, "get_status_display") else (record.status or ""),
                f"{(lease_balance or 0):,.2f}", f"{(security_balance or 0):,.2f}",
                f"{(total_balance or 0):,.2f}" if total_balance is not None else "",
                f"{(record.amount or 0):,.2f}", items_payload,
            )
        else:
            whatsapp = mark_safe(
                '<button type="button" class="btn btn-sm btn-secondary action-btn" title="No phone number" disabled>'
                '<i class="fab fa-whatsapp"></i><span class="btn-text ms-1">WhatsApp</span></button>'
            )

        return format_html(
            '<div class="d-flex actions-wrap">'
            '<a href="{}" class="btn btn-sm btn-info action-btn btn-view"><i class="fas fa-eye"></i><span class="btn-text ms-1">View</span></a>'
            '<a href="{}" class="btn btn-sm btn-warning action-btn btn-edit"><i class="fas fa-edit"></i><span class="btn-text ms-1">Edit</span></a>'
            '<a href="{}" class="btn btn-sm btn-danger action-btn btn-delete"><i class="fas fa-trash"></i><span class="btn-text ms-1">Delete</span></a>'
            '{}'
            '<a href="{}" class="btn btn-sm btn-success action-btn btn-pay"><i class="fas fa-money-bill-wave"></i><span class="btn-text ms-1">Pay</span></a>'
            '</div>',
            view_url, edit_url, delete_url, whatsapp, pay_url,
        )



class InvoiceListView(tables.SingleTableView):
    model = Invoice
    table_class = InvoiceTable
    template_name = 'invoices/invoice_list.html'

    def get(self, request, *args, **kwargs):
        resp = handle_export(request, self.get_table(), 'invoices')
        return resp or super().get(request, *args, **kwargs)

# invoices/tables.py
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
import django_tables2 as tables
from django_tables2.columns import DateColumn, Column

# NOTE: adjust to your actual app structure if different
from .models import Invoice
# your existing base table (keep if you use it)
from properties.tables import ExportableTable


class InvoiceTable(ExportableTable):
    """
    Invoice list table with requested columns & formatting:
      - No Lease# / Status
      - Serial# = invoice_number (always sorted)
      - Tenant first name (<=20 chars)
      - Property column
      - Description before Issue Date and <=30 chars
      - Rs currency
      - Issue/Due dates wider and non-wrapping
    """

    # Optional running row number
    sn = tables.Column(verbose_name="#", empty_values=(), orderable=False)

    # Serial# maps to model's invoice_number
    invoice_number = tables.Column(
        verbose_name="Serial#",
        accessor="invoice_number",
        orderable=True,
    )

    tenant = Column(empty_values=(), verbose_name="Tenant (First)")
    property_name = Column(empty_values=(), verbose_name="Property")

    # Description should appear BEFORE issue_date in sequence (see Meta.sequence)
    description = Column(accessor="description", verbose_name="Description")

    issue_date = DateColumn(verbose_name="Issue Date",
                            accessor="issue_date", format="Y-m-d")
    due_date = DateColumn(verbose_name="Due Date",
                          accessor="due_date", format="Y-m-d")

    # or "total" if that's your field
    amount = Column(accessor="amount", verbose_name="Amount")

    actions = Column(empty_values=(), orderable=False, verbose_name="")

    class Meta(ExportableTable.Meta):
        model = Invoice
        fields = (
            "sn",
            "invoice_number",  # Serial#
            "tenant",
            "property_name",
            "description",     # <-- before dates
            "issue_date",
            "due_date",
            "amount",
            "actions",
        )
        sequence = fields
        order_by = ("invoice_number",)  # always sorted by Serial#
        attrs = {"class": "table table-striped table-hover align-middle"}

    # ---------- Renderers ----------

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sn = 0

    def render_sn(self, record):
        """Stable row ordinal across pages."""
        self._sn += 1
        # add pagination offset if available
        page = getattr(self, "page", None)
        if page and hasattr(page, "number") and hasattr(page, "paginator"):
            offset = (page.number - 1) * page.paginator.per_page
        else:
            offset = 0
        return offset + self._sn

    def render_tenant(self, record):
        """First name up to 20 chars."""
        first = ""
        try:
            first = (getattr(record.lease.tenant,
                     "first_name", "") or "").strip()
        except Exception:
            pass
        first = (first or "")[:20]
        return first or "—"

    def render_property_name(self, record):
        """Property name via lease -> unit -> property if available."""
        try:
            # Common structures: lease.unit.property.name OR lease.property.name
            unit = getattr(record.lease, "unit", None)
            if unit and getattr(unit, "property", None):
                return getattr(unit.property, "name", None) or "—"
            prop_via_lease = getattr(record.lease, "property", None)
            if prop_via_lease:
                return getattr(prop_via_lease, "name", None) or "—"
        except Exception:
            pass
        return "—"

    def render_description(self, value):
        """Max 30 chars, tooltip with full text, non-wrapping."""
        text = (value or "").strip()
        show = (text[:30] + "…") if len(text) > 30 else text
        return mark_safe(
            f'<span class="d-inline-block text-truncate" style="max-width:30ch" title="{text}">{show}</span>'
        )

    def render_issue_date(self, value):
        return mark_safe(f'<span class="text-nowrap">{value:%Y-%m-%d}</span>') if value else "—"

    def render_due_date(self, value):
        return mark_safe(f'<span class="text-nowrap">{value:%Y-%m-%d}</span>') if value else "—"

    def render_amount(self, value):
        """Prefix with Rs and format."""
        if value is None:
            return "—"
        try:
            num = float(value)
            formatted = f"{num:,.2f}"
        except Exception:
            formatted = value
        return mark_safe(f'<span class="text-nowrap">Rs {formatted}</span>')

    def render_actions(self, record):
        """
        Adjust URL names to your routes if different.
        Expected names:
          - invoices:invoice_detail
          - invoices:invoice_update
          - invoices:invoice_pdf
        """
        view_url = reverse("invoices:invoice_detail", args=[record.pk])
        edit_url = reverse("invoices:invoice_update", args=[record.pk])
        pdf_url = reverse("invoices:invoice_pdf", args=[record.pk])
        return format_html(
            '<div class="btn-group btn-group-sm" role="group">'
            '  <a class="btn btn-outline-secondary" href="{}" title="View"><i class="fas fa-eye"></i></a>'
            '  <a class="btn btn-outline-primary" href="{}" title="Edit"><i class="fas fa-edit"></i></a>'
            '  <a class="btn btn-outline-dark" href="{}" title="PDF"><i class="fas fa-file-pdf"></i></a>'
            "</div>",
            view_url,
            edit_url,
            pdf_url,
        )

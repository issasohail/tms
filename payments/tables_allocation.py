# payments/tables_allocation.py
import django_tables2 as tables
from django.utils.html import format_html
from django.urls import reverse

from payments.models import PaymentAllocation


class AllocationTable(tables.Table):
    sn = tables.Column(empty_values=(), orderable=False)

    reference = tables.Column(empty_values=(), verbose_name="Ref", orderable=False)
    tenant = tables.Column(empty_values=(), orderable=False)
    date = tables.Column(empty_values=(), orderable=False)

    lease_amount = tables.Column()
    security_amount = tables.Column()
    security_type = tables.Column()

    actions = tables.Column(empty_values=(), orderable=False)

    def render_sn(self):
        self.row_counter = getattr(self, "row_counter", 0) + 1
        return self.row_counter

    def render_reference(self, record):
        p = record.payment
        return getattr(p, "reference_number", "") or f"#{p.id}"

    def render_tenant(self, record):
        t = record.payment.lease.tenant
        return f"{t.first_name} {t.last_name}".strip()

    def render_date(self, record):
        return record.payment.payment_date

    def render_actions(self, record):
        view = reverse("payments:allocation_detail", args=[record.id])
        pdf = reverse("payments:allocation_pdf", args=[record.id])
        return format_html(
            '<a class="btn btn-sm btn-primary" href="{}">View</a> '
            '<a class="btn btn-sm btn-secondary" href="{}">PDF</a>',
            view, pdf
        )

    class Meta:
        template_name = "django_tables2/bootstrap5-responsive.html"
        attrs = {"class": "table table-sm table-bordered table-hover align-middle"}
        fields = (
            "sn", "reference", "tenant", "date",
            "lease_amount", "security_amount", "security_type",
            "actions"
        )
        export_formats = ["csv", "xlsx"]

import django_tables2 as tables
from django.urls import reverse
from django.utils.html import format_html


class PaymentDetailTable(tables.Table):
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
        payment = record.payment
        return getattr(payment, "reference_number", "") or f"#{payment.id}"

    def render_tenant(self, record):
        tenant = record.payment.lease.tenant
        return f"{tenant.first_name} {tenant.last_name}".strip()

    def render_date(self, record):
        return record.payment.payment_date

    def render_actions(self, record):
        payment_id = record.payment_id
        view = reverse("payments:payment_detail", args=[payment_id])
        pdf = reverse("payments:payment_pdf", args=[payment_id])
        return format_html(
            '<a class="btn btn-sm btn-primary" href="{}">View</a> '
            '<a class="btn btn-sm btn-secondary" href="{}">PDF</a>',
            view,
            pdf,
        )

    class Meta:
        template_name = "django_tables2/bootstrap5-responsive.html"
        attrs = {"class": "table table-sm table-bordered table-hover align-middle"}
        fields = (
            "sn", "reference", "tenant", "date",
            "lease_amount", "security_amount", "security_type",
            "actions",
        )
        export_formats = ["csv", "xlsx"]

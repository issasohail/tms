import django_tables2 as tables
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from decimal import Decimal, InvalidOperation
from django.urls import reverse

def _truncate(s, n):
        s = (s or "").strip()
        return (s[:n] + "…") if len(s) > n else s

def _to_decimal(v):
    if v is None:
        return Decimal("0.00")
    if isinstance(v, Decimal):
        return v
    s = str(v).replace(",", "").replace("Rs.", "").replace("Rs", "").strip()
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


class CashLedgerTable(tables.Table):
    sn = tables.Column(empty_values=(), orderable=False, attrs={"td": {"class": "text-center col-sn"}, "th": {"class": "col-sn"}})
    source = tables.Column(verbose_name="Src")

    tenant = tables.Column(accessor="lease.tenant",
                       attrs={"td": {"class": "col-tenant"}, "th": {"class": "col-tenant"}})

    property = tables.Column(accessor="lease.unit.property.property_name",
                            attrs={"td": {"class": "col-property"}, "th": {"class": "col-property"}})

    unit = tables.Column(accessor="lease.unit.unit_number",
                        attrs={"td": {"class": "col-unit"}, "th": {"class": "col-unit"}})

    date = tables.DateColumn(verbose_name="Date",
                            attrs={"td": {"class": "col-date"}, "th": {"class": "col-date"}})

    amount = tables.Column(verbose_name="Amount",
                        attrs={"td": {"class": "col-amount"}, "th": {"class": "col-amount"}})

    method = tables.Column(verbose_name="Method",
                        attrs={"td": {"class": "col-method"}, "th": {"class": "col-method"}})

    lease_balance = tables.Column(
        verbose_name="Lease Bal",
        attrs={"td": {"class": "col-lease-balance"}, "th": {"class": "col-lease-balance"}},
    )

    security_balance = tables.Column(
        verbose_name="Security Bal",
        attrs={"td": {"class": "col-security-balance"}, "th": {"class": "col-security-balance"}},
    )

    description = tables.Column(verbose_name="Desc", empty_values=(), orderable=False,
                                attrs={"td": {"class": "col-description"}, "th": {"class": "col-description"}})

    balance = tables.Column(verbose_name="Total Bal", empty_values=(), orderable=False,
                            attrs={"td": {"class": "text-center col-balance"}, "th": {"class": "text-center col-balance"}})

    actions = tables.Column(empty_values=(), orderable=False,
                            attrs={"td": {"class": "text-nowrap col-actions actions-cell"},
                                "th": {"class": "col-actions actions-cell"}})

    def render_sn(self):
        self.row_counter = getattr(self, "row_counter", 0) + 1
        return self.row_counter

    def render_source(self, value, record):
        if record.source == "PAYMENT":
            cls = "bg-primary"
            label = "Payment"
        else:
            cls = "bg-warning text-dark"
            label = "Security"

        if getattr(record, "is_split", False):
            label = f"{label} • Split"

        return format_html('<span class="badge {}">{}</span>', cls, label)



    def render_property(self, value, record):
        # value might already be property name depending on accessor
        full = str(value or "")
        short = _truncate(full, 8)
        return format_html('<span title="{}">{}</span>', full, short)


    def render_amount(self, value, record):
        amt = _to_decimal(record.amount)
        css = "text-danger" if amt < 0 else "text-success"

        total_s = f"{amt:,.2f}"

        if getattr(record, "is_split", False):
            lease_amt = _to_decimal(getattr(record, "lease_amount", 0))
            sec_amt   = _to_decimal(getattr(record, "security_amount", 0))

            lease_s = f"{lease_amt:,.2f}"
            sec_s   = f"{sec_amt:,.2f}"

            return format_html(
                '<div class="{} fw-semibold">Rs. {}</div>'
                '<div class="small text-muted">Lease: Rs. {} | Security: Rs. {}</div>',
                css,
                total_s,
                lease_s,
                sec_s,
            )

        return format_html(
            '<span class="{} fw-semibold">Rs. {}</span>',
            css,
            total_s
        )


    def render_tenant(self, value, record):
        full = f"{record.lease.tenant.first_name} {record.lease.tenant.last_name}".strip()
        short = (full[:15] + "…") if len(full) > 15 else full
        url = reverse("tenants:tenant_detail", args=[record.lease.tenant.pk])
        return format_html(
            '<a href="{}" title="{}" class="text-decoration-none">{}</a>',
            url, full, short
        )

    def render_lease_balance(self, value, record):
        return f"Rs. {_to_decimal(record.lease_balance):,.2f}"

    def render_security_balance(self, value, record):
        return f"Rs. {_to_decimal(record.security_balance):,.2f}"

    def render_balance(self, value, record):
        total = _to_decimal(record.lease_balance) + _to_decimal(record.security_balance)
        return f"Rs. {total:,.2f}"

    
    def render_description(self, value, record):
        full = str(getattr(record, "description", "") or "")
        return format_html('<span title="{}">{}</span>', full, _truncate(full, 12))

    def render_method(self, value, record):
        full = str(value or "")
        return format_html('<span title="{}">{}</span>', full, _truncate(full, 12))

    def render_actions(self, value, record):
        btns = []

        view_url = getattr(record, "view_url", None)
        edit_url = getattr(record, "edit_url", None)
        delete_url = getattr(record, "delete_url", None)
        wa_url = getattr(record, "wa_url", None)
        allocation_id = getattr(record, "allocation_id", None)

        if view_url:
            btns.append(format_html(
                '<a href="{}" class="btn btn-sm btn-primary" title="View">'
                '<i class="fas fa-eye"></i></a>',
                view_url
            ))

        if edit_url:
            btns.append(format_html(
                '<a href="{}" class="btn btn-sm btn-secondary" title="Edit">'
                '<i class="fas fa-edit"></i></a>',
                edit_url
            ))

        if delete_url:
            btns.append(format_html(
                '<a href="{}" class="btn btn-sm btn-danger" title="Delete">'
                '<i class="fas fa-trash"></i></a>',
                delete_url
            ))

        if wa_url:
            # JS will read data-wa-url and open it
            btns.append(format_html(
                '<button type="button" class="btn btn-sm btn-success btn-wa-receipt" '
                'data-wa-url="{}" title="WhatsApp Receipt">'
                '<i class="fab fa-whatsapp"></i></button>',
                wa_url
            ))

        # Inline split edit button (modal)
        if allocation_id:
            btns.append(format_html(
                '<button type="button" class="btn btn-sm btn-warning btn-split-edit" '
                'data-allocation-id="{}" title="Edit Split">'
                '<i class="fas fa-random"></i></button>',
                allocation_id
            ))

        if not btns:
            return ""

        return format_html('<div class="actions-wrap">{}</div>', mark_safe(" ".join(btns)))
        
    class Meta:
        template_name = "django_tables2/bootstrap5-responsive.html"
        attrs = {"class": "table table-sm table-bordered table-hover align-middle"}
        fields = (
            "sn", "source", "tenant", "property", "unit",
            "date", "amount", "method","description",
            "lease_balance", "security_balance", "balance",
            "actions"
        )
        export_formats = ["csv", "xlsx", "pdf"]

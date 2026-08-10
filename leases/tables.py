# leases/tables.py
import json
from decimal import Decimal

from django.core.cache import cache
from django.middleware.csrf import get_token
from django.db.models import Sum
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django_tables2 import SingleTableView, tables
from django_tables2.columns import DateColumn

from core.currency import format_money
from core.models import GlobalSettings
from core.utils.identity import whatsapp_phone_digits
from properties.tables import ExportableTable
from utils.pdf_export import handle_export

from .models import Lease
from .services.lease_expiry import get_lease_expiry_countdown


def _global_settings():
    settings_obj = cache.get("core.global_settings")
    if settings_obj is None:
        settings_obj = GlobalSettings.get_solo()
        cache.set("core.global_settings", settings_obj, 60)
    return settings_obj


class LeaseTable(ExportableTable):
    export_exclude = ("id", "police_verification")
    export_verbose_names = {
        "sn": "S.N.",
        "family_members": "Family",
        "security_due": "Sec. Balance",
    }
    excel_include_property = True
    compact_export_heading = True
    pdf_export_attrs = {
        **ExportableTable.Meta.pdf_export_attrs,
        "orientation": "landscape",
        "column_widths": {
            "sn": 28,
            "tenant": 85,
            "unit": 75,
            "owner": 70,
            "family_members": 38,
            "bill_water_charges": 36,
            "vehicle_info": 42,
            "monthly_payments": 60,
            "due_date": 32,
            "status": 38,
            "start_date": 60,
            "end_date": 60,
            "balance": 60,
            "security_due": 45,
        },
    }

    id = tables.Column(
        verbose_name="ID",
        linkify=lambda record: reverse("leases:lease_detail", args=[record.pk]),
        attrs={"td": {"class": "col-id"}, "th": {"class": "col-id"}},
    )

    tenant = tables.Column(
        accessor="tenant",
        order_by=("tenant__first_name", "tenant__last_name"),
        verbose_name="Tenant",
        linkify=lambda record: reverse(
            "tenants:tenant_detail", args=[record.tenant.pk]
        ),
        attrs={"td": {"class": "col-tenant"}, "th": {"class": "col-tenant"}},
    )

    property = tables.Column(
        accessor="unit.property.property_name",
        verbose_name="Property",
        linkify=lambda record: reverse(
            "properties:property_detail", args=[record.unit.property.pk]
        ),
        attrs={
            "td": {"class": "col-property"},
            "th": {"class": "col-property"},
        },  # keep
    )

    unit = tables.Column(
        accessor="unit.unit_number",
        verbose_name="Unit",
        attrs={
            "td": {"class": "col-unit"},
            "th": {"class": "col-unit"},
        },
    )

    owner = tables.Column(
        accessor="unit.property.owner_name",
        verbose_name="Owner",
        orderable=False,
        attrs={
            "td": {"class": "col-owner"},
            "th": {"class": "col-owner"},
        },
    )

    family_members = tables.Column(
        verbose_name="Family Member",
        order_by=("family_member_count",),
        empty_values=(),
        attrs={
            "td": {"class": "col-family"},
            "th": {"class": "col-family"},
        },
    )

    police_verification = tables.Column(
        verbose_name="Police",
        orderable=False,
        empty_values=(),
        attrs={
            "td": {"class": "col-police"},
            "th": {"class": "col-police"},
        },
    )

    bill_water_charges = tables.Column(
        verbose_name="Bill Water",
        orderable=True,
        empty_values=(),
        attrs={
            "td": {"class": "col-bill-water text-center"},
            "th": {"class": "col-bill-water text-center"},
        },
    )

    vehicle_info = tables.Column(
        verbose_name="Vehicle",
        orderable=False,
        empty_values=(),
        attrs={
            "td": {"class": "col-vehicle text-center"},
            "th": {"class": "col-vehicle text-center"},
        },
    )

    status = tables.Column(
        attrs={"td": {"class": "col-status"}, "th": {"class": "col-status"}}
    )

    start_date = DateColumn(
        format="M d, Y",
        verbose_name="Start Date",
        attrs={"td": {"class": "col-start"}, "th": {"class": "col-start"}},  # ✨ add
    )
    end_date = DateColumn(
        format="M d, Y",
        verbose_name="End Date",
        attrs={"td": {"class": "col-end"}, "th": {"class": "col-end"}},  # ✨ add
    )

    balance = tables.Column(
        accessor="list_balance",
        verbose_name="Balance",
        linkify=lambda record: reverse("leases:lease_ledger_by_pk", args=[record.pk]),
        attrs={
            "td": {"class": "col-balance text-center"},
            "th": {"class": "col-balance text-end"},
        },
        orderable=False,
    )

    security_due = tables.Column(
        accessor="list_security_due",
        verbose_name="Sec. Due",
        attrs={
            "td": {"class": "col-sec text-end"},
            "th": {"class": "col-sec text-end"},
        },
        orderable=False,
    )

    monthly_payments = tables.Column(
        accessor="list_monthly_payment",
        verbose_name=mark_safe("Monthly\nPayment"),
        attrs={
            "td": {"class": "col-monthly text-end"},
            "th": {"class": "col-monthly monthly-col"},
        },  # ✨ add td class
    )

    actions = tables.Column(
        verbose_name="Actions",
        orderable=False,
        empty_values=(),
        attrs={
            "td": {"class": "col-actions actions-cell"},
            "th": {"class": "col-actions actions-cell"},
        },
    )
    due_date = tables.Column(
        verbose_name="Due Date",
        attrs={
            "td": {"class": "col-due-date text-center"},
            "th": {"class": "col-due-date text-center"},
        },
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.global_settings = _global_settings()
        # Attach classes to the auto-added 'sn' column so we can style it
        if "sn" in self.columns:
            bc = self.columns["sn"]  # BoundColumn
            col = bc.column  # TemplateColumn
            # <-- use verbose_name (header has no setter)
            col.verbose_name = "SN#"

            # merge attrs so we don't clobber anything already set
            existing = col.attrs or {}
            th = (existing.get("th") or {}).copy()
            td = (existing.get("td") or {}).copy()
            th["class"] = (th.get("class", "") + " col-sn").strip()
            td["class"] = (td.get("class", "") + " col-sn").strip()
            col.attrs = {**existing, "th": th, "td": td}

    def render_monthly_payments(self, value):
        """Render total payments using the model's monthly_payments property"""
        # payments = record.total_payments
        return format_money(value, self.global_settings, decimals=0)

    def render_due_date(self, value, record):
        full = value or ""
        short = full[:4] if full else "-"
        request = getattr(self, "request", None)
        if request and request.GET.get("_export"):
            return short
        url = reverse("leases:lease_due_date_inline_update", args=[record.pk])
        return mark_safe(
            f'''
            <button
                type="button"
                class="lease-due-date-badge"
                data-url="{escape(url)}"
                data-value="{escape(full)}"
                title="{escape(full or 'Click to set due date')}"
            >
                {escape(short)}
            </button>
            '''
        )

    def render_vehicle_info(self, record):
        vehicle_count = getattr(record, "vehicle_count", 0) or 0
        pending_count = getattr(record, "pending_vehicle_count", 0) or 0
        has_vehicle = getattr(record, "has_vehicle", None)
        request = getattr(self, "request", None)
        if request and request.GET.get("_export"):
            if vehicle_count:
                return "Yes" if vehicle_count == 1 else f"Yes {vehicle_count}"
            if pending_count:
                return "Pending" if pending_count == 1 else f"Pending {pending_count}"
            if has_vehicle is False:
                return "No"
            if has_vehicle is True:
                return "Need Info"
            return "Not Set"
        url = reverse("leases:lease_vehicle_info_ajax", args=[record.pk])

        if vehicle_count:
            label = "Yes" if vehicle_count == 1 else f"Yes {vehicle_count}"
            return mark_safe(
                f'<button type="button" class="vehicle-info-pill vehicle-info-pill--yes" '
                f'data-vehicle-url="{escape(url)}" '
                f'title="{vehicle_count} active vehicle record(s)">{escape(label)}</button>'
            )
        if pending_count:
            label = "Pending" if pending_count == 1 else f"Pending {pending_count}"
            return mark_safe(
                f'<button type="button" class="vehicle-info-pill vehicle-info-pill--pending" '
                f'data-vehicle-url="{escape(url)}" '
                f'title="{pending_count} pending vehicle submission(s)">{escape(label)}</button>'
            )
        if has_vehicle is False:
            return mark_safe(
                f'<button type="button" class="vehicle-info-pill vehicle-info-pill--no" '
                f'data-vehicle-url="{escape(url)}" '
                f'title="Tenant confirmed no vehicle">No Vehicle</button>'
            )
        if has_vehicle is True:
            return mark_safe(
                f'<button type="button" class="vehicle-info-pill vehicle-info-pill--pending" '
                f'data-vehicle-url="{escape(url)}" '
                f'title="Tenant has vehicle but details are missing">Need Info</button>'
            )
        return mark_safe(
            f'<button type="button" class="vehicle-info-pill vehicle-info-pill--missing" '
            f'data-vehicle-url="{escape(url)}" '
            f'title="No vehicle information has been entered">Not Entered</button>'
        )


    def render_family_members(self, record):
        count = getattr(record, "family_member_count", 0) or 0
        pending = getattr(record, "pending_family_count", 0) or 0
        request = getattr(self, "request", None)
        if request and request.GET.get("_export"):
            return str(count)
        url = reverse("leases:lease_detail", args=[record.pk]) + "#leaseFamilySection"

        label = f"{count} Member" if count == 1 else f"{count} Members"
        pending_html = ""
        if pending:
            pending_html = f'<span class="family-pending">{pending} Pending</span>'

        return mark_safe(
            f'<a href="{url}" class="family-count-pill">{escape(label)}</a>{pending_html}'
        )

    def render_police_verification(self, record):
        has_document = bool(getattr(record, "police_document_count", 0)) or bool(
            getattr(record, "police_verification_document", "")
        )
        if has_document:
            return mark_safe('<span class="police-ok">✓ Yes</span>')

        request = getattr(self, "request", None)
        if not request:
            return mark_safe('<span class="police-missing">No</span>')
        url = reverse("leases:lease_police_verification_link", args=[record.pk])
        csrf = get_token(request)
        return mark_safe(
            '<form method="post" action="{url}" class="police-link-form">'
            '<input type="hidden" name="csrfmiddlewaretoken" value="{csrf}">'
            '<span class="police-missing">No</span>'
            '<button type="submit" class="police-send-btn" title="Send police verification link">Send</button>'
            '</form>'.format(url=escape(url), csrf=escape(csrf))
        )

    def render_bill_water_charges(self, value, record):
        label = "Yes" if value else "No"
        request = getattr(self, "request", None)
        if request and request.GET.get("_export"):
            return label
        badge_class = "bg-success" if value else "bg-secondary"
        url = reverse("leases:lease_bill_water_inline_update", args=[record.pk])

        return mark_safe(
            f'''
            <button
                type="button"
                class="badge {badge_class} lease-water-badge border-0"
                data-url="{escape(url)}"
                data-value="{"1" if value else "0"}"
            >
                {label}
            </button>
            '''
        )

    def render_balance(self, value, record):
        """Format balance for display and exports"""
        formatted_value = format_money(value, self.global_settings, decimals=0)

        # For exports, just return the formatted value
        if hasattr(self, "export_formats") and getattr(self, "is_export", False):
            return formatted_value

        # For HTML display, return the formatted value (it will be automatically linked)
        return formatted_value

    def render_security_due(self, value, record):
        formatted = format_money(value, self.global_settings, decimals=0)

        # Exports (CSV/XLSX/PDF)
        if hasattr(self, "export_formats") and getattr(self, "is_export", False):
            return formatted

        # HTML: highlight if > 0
        if value and value > 0:
            return mark_safe(f'<span class="text-danger fw-bold">{formatted}</span>')
        return formatted

    def render_start_date(self, value):
        """Format date for exports"""
        return value.strftime("%Y-%m-%d") if value else ""

    def render_end_date(self, value, record):
        """Format date for display and add styling if ending soon"""
        formatted_date = value.strftime("%Y-%m-%d") if value else ""

        # For exports, just return the formatted date
        # For exports (CSV, Excel), return simple formatted date
        request = getattr(self, "request", None)
        if request and request.GET.get("_export"):
            return value.strftime("%Y-%m-%d")

        if hasattr(self, "export_formats") and getattr(self, "is_export", False):
            return value.strftime("%Y-%m-%d")

        # For HTML display, show the same renewal action where the end date is red.
        if value:
            countdown = get_lease_expiry_countdown(record)
            if countdown:
                days_label = countdown.label
                renew_button = ""
                tenant_phone = getattr(getattr(record, "tenant", None), "phone", "")
                if tenant_phone:
                    renew_button = (
                        f'<button type="button" '
                        f'class="btn btn-primary btn-wa-renew ll-renew-btn" '
                        f'title="Send expiry and renewal WhatsApp notices" '
                        f'data-object-id="{record.pk}">'
                        f'Renew'
                        f'</button>'
                    )
                return mark_safe(
                    f'<span class="ending-soon">{value.strftime("%b %d, %Y")}</span>'
                    f'<span class="ll-renew-line">'
                    f'<small class="ll-days-left">{days_label}</small>'
                    f'{renew_button}'
                    f'</span>'
                )

        return value.strftime("%b %d, %Y") if value else ""

    def render_end_date1(self, value):
        """Format date for exports"""
        return value.strftime("%Y-%m-%d") if value else ""

    def render_property(self, value, record):
        prop = record.unit.property
        full = value or ""
        short = (full[:8] + "…") if len(full) > 8 else full
        url = reverse("properties:property_detail", args=[prop.pk])
        owner_name = (getattr(prop, "owner_name", "") or "").strip()
        owner_phone = getattr(prop, "owner_phone", "") or ""
        owner_html = ""
        if owner_name:
            owner_url = f"{url}#owner-information"
            wa_digits = whatsapp_phone_digits(
                owner_phone,
                getattr(self.global_settings, "country_code", "") or "",
            )
            wa_html = (
                f'<a class="ll-owner-wa" href="https://wa.me/{escape(wa_digits)}" '
                f'target="_blank" rel="noopener" title="WhatsApp owner"><i class="fab fa-whatsapp"></i></a>'
                if wa_digits else ""
            )
            owner_html = (
                f'<span class="ll-owner-info">Owner: '
                f'<a class="ll-owner-link" href="{escape(owner_url)}" title="Open owner details">{escape(owner_name)}</a>'
                f'{wa_html}</span>'
            )
        return mark_safe(
            f'<span class="ll-property-name"><a href="{url}" title="{escape(full)}">{escape(short)}</a></span>{owner_html}'
        )

    def render_unit(self, value, record):
        """Render the live Property/Unit cell with owner details beneath it.

        The standalone ``property`` column is intentionally excluded from the
        live Lease table, so owner information must be emitted by the visible
        Unit cell for both desktop rows and the existing mobile-card builder.
        """
        unit = record.unit
        prop = unit.property
        unit_text = str(value or "")
        unit_url = reverse("properties:unit_detail", args=[unit.pk])
        property_url = reverse("properties:property_detail", args=[prop.pk])

        owner_name = (getattr(prop, "owner_name", "") or "").strip()
        owner_phone = getattr(prop, "owner_phone", "") or ""
        owner_html = ""
        if owner_name:
            owner_url = f"{property_url}#owner-information"
            wa_digits = whatsapp_phone_digits(
                owner_phone,
                getattr(self.global_settings, "country_code", "") or "",
            )
            wa_html = (
                f'<a class="ll-owner-wa" href="https://wa.me/{escape(wa_digits)}" '
                f'target="_blank" rel="noopener" title="WhatsApp owner"><i class="fab fa-whatsapp"></i></a>'
                if wa_digits else ""
            )
            owner_html = (
                f'<span class="ll-owner-info">Owner: '
                f'<a class="ll-owner-link" href="{escape(owner_url)}" title="Open owner details">{escape(owner_name)}</a>'
                f'{wa_html}</span>'
            )

        return mark_safe(
            f'<a href="{escape(unit_url)}" title="{escape(unit_text)}">{escape(unit_text)}</a>'
            f'{owner_html}'
        )

    def value_unit(self, value, record):
        """Export Unit as plain unit text only; owner has its own export column."""
        return str(value or "")

    def render_owner(self, value, record):
        return (value or "").strip()

    def value_owner(self, value, record):
        return (value or "").strip()

    def value_security_due(self, value, record):
        """Export security balance as plain text, never rendered HTML."""
        return format_money(value, self.global_settings, decimals=0)

    def value_tenant(self, value, record):
        """Use full tenant names in spreadsheet/text exports."""
        tenant = getattr(record, "tenant", None)
        full = tenant.get_full_name().strip() if tenant else ""
        request = getattr(self, "request", None)
        export_format = request.GET.get("_export") if request else ""
        if export_format in {"xlsx", "csv"}:
            return full
        return (full[:15] + "...") if len(full) > 15 else full

    def render_tenant(self, record, value):
        t = record.tenant
        full = f"{t.first_name} {t.last_name}".strip() if t else ""
        short = (full[:15] + "...") if len(full) > 15 else full
        request = getattr(self, "request", None)
        if request and request.GET.get("_export"):
            return short
        return mark_safe(
            f'<span class="tenant-text" title="{escape(full)}">{escape(short)}</span>'
        )

    def render_actions(self, record):
        # Monthly / normal balance (rent, maintenance, etc.)
        base_balance = getattr(record, "list_balance", Decimal("0.00")) or Decimal(
            "0.00"
        )

        # Security deposit balance (to collect)
        sec_balance_dec = getattr(
            record, "list_security_due", Decimal("0.00")
        ) or Decimal("0.00")

        # Combined total due
        total_due = base_balance + sec_balance_dec

        has_balance = total_due > Decimal("0.00")
        tenant_phone = record.tenant.phone or ""
        period_start = ""
        period_end = ""
        security_required = ""
        security_balance = ""
        security_status = ""
        due_date = record.due_date or ""
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
            security_required = format_money(sec_required_dec, self.global_settings)

            # --- Security deposit balance & status ---
            security_balance = (
                format_money(sec_balance_dec, self.global_settings)
                if sec_balance_dec > 0
                else ""
            )
            security_status = "Pending" if sec_balance_dec > 0 else "Paid"
            whatsapp_url = (
                "sendWhatsAppReminder("
                f"{json.dumps(tenant_phone)}, "
                f"{json.dumps(record.tenant.first_name)}, "
                f"{json.dumps(record.unit.property.property_name)}, "
                f"{json.dumps(record.unit.unit_number)}, "
                f"{float(total_due)}, "  # 👈 now passing total (monthly + security)
                f"{json.dumps(period_start)}, "
                f"{json.dumps(period_end)}, "
                f"{json.dumps(security_required)}, "
                f"{json.dumps(security_status)}, "
                f"{json.dumps(security_balance)}, "
                f"{record.pk}, "
                f"{json.dumps(due_date)}"
                ")"
            )

        return render_to_string(
            "components/action_buttons.html",
            {
                "record": record,
                "view_url": reverse("leases:lease_detail", args=[record.pk]),
                "edit_url": reverse("leases:lease_update", args=[record.pk]),
                "delete_url": reverse("leases:lease_delete", args=[record.pk]),
                "make_payment_url": reverse("payments:payment_create")
                + f"?lease={record.pk}",
                "view_ledger_url": reverse("leases:lease_ledger_by_pk", args=[record.pk]),
                "whatsapp_url": whatsapp_url,
                "has_balance": has_balance,
                "is_lease_row": True,
                "lease_reminder_phone": tenant_phone,
                "lease_reminder_first_name": record.tenant.first_name,
                "lease_reminder_property": record.unit.property.property_name,
                "lease_reminder_unit": record.unit.unit_number,
                "lease_reminder_balance": float(total_due),
                "lease_reminder_period_start": period_start,
                "lease_reminder_period_end": period_end,
                "lease_reminder_security_required": security_required,
                "lease_reminder_security_status": security_status,
                "lease_reminder_security_balance": security_balance,
                "lease_reminder_due_date": due_date,
                "lease_reminder_lease_id": record.pk,
            },
        )

    class Meta(ExportableTable.Meta):
        model = Lease
        fields = (
            "sn",
            "id",
            "tenant",
            "unit",
            "owner",
            "family_members",
            "police_verification",
            "bill_water_charges",
            "vehicle_info",
            "monthly_payments",
            "due_date",
            "status",
            "start_date",
            "end_date",
            "balance",
            "security_due",
            "actions",
        )
        sequence = fields
        exclude = ("property",)
        order_by = ("unit",)
        export_formats = ["csv", "xlsx", "pdf", "jpg"]


class LeaseListView(SingleTableView):
    model = Lease
    table_class = LeaseTable
    template_name = "leases/lease_list.html"

    def get_queryset(self):
        queryset = super().get_queryset()

        # Annotate with total payments
        queryset = queryset.annotate(total_payments=Sum("payments__amount"))

        # Filter for non-zero balance if requested
        if self.request.GET.get("nonzero_balance") == "on":
            queryset = queryset.annotate(
                calculated_balance=F("monthly_rent") - F("total_payments")
            ).filter(calculated_balance__gt=0)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["nonzero_balance"] = self.request.GET.get("nonzero_balance", "")
        return context

    def get(self, request, *args, **kwargs):
        resp = handle_export(request, self.get_table(), "leases")
        return resp or super().get(request, *args, **kwargs)


# --- TENANT DETAIL LEASE TABLE -----------------------


class TenantLeaseTable(ExportableTable):
    # NOTE: ExportableTable in your project already supports 'sn' numbering,
    # since LeaseTable uses it in Meta.fields. We’ll include 'sn' here too. :contentReference[oaicite:0]{index=0}

    property = tables.Column(
        accessor="unit.property.property_name",
        verbose_name="Property",
        linkify=lambda record: reverse(
            "properties:property_detail", args=[record.unit.property.pk]
        ),
        attrs={"td": {"class": "col-property"}},
    )

    unit = tables.Column(
        accessor="unit.unit_number",
        verbose_name="Unit",
        linkify=lambda record: reverse("properties:unit_detail", args=[record.unit.pk]),
        attrs={"td": {"class": "col-unit-wide"}},
    )

    status = tables.Column(
        verbose_name="Status",
        attrs={"td": {"class": "text-center"}, "th": {"class": "text-center"}},
    )

    start_date = DateColumn(
        format="Y-m-d", verbose_name="Start Date", attrs={"td": {"class": "col-date"}}
    )
    end_date = DateColumn(
        format="Y-m-d", verbose_name="End Date", attrs={"td": {"class": "col-date"}}
    )

    monthly_payment = tables.Column(
        # you already expose this on Lease; your LeaseTable uses it too :contentReference[oaicite:1]{index=1}
        accessor="get_monthly_payment",
        verbose_name="Monthly Payment",
        attrs={"td": {"class": "text-end"}, "th": {"class": "text-center monthly-col"}},
    )

    balance = tables.Column(
        accessor="list_balance",
        verbose_name="Balance",
        attrs={"td": {"class": "text-end"}, "th": {"class": "text-end"}},
        orderable=False,
    )

    actions = tables.Column(
        verbose_name="Actions",
        orderable=False,
        attrs={"td": {"class": "col-actions"}, "th": {"class": "col-actions"}},
    )

    # ---- renderers ----
    def render_monthly_payment(self, value):
        return f"{int(value):,}" if value else "0.00"

    def render_balance(self, value):
        return f"{int(value):,}" if value else "0.00"

    def render_end_date(self, value):
        return value.strftime("%Y-%m-%d") if value else ""

    def render_start_date(self, value):
        return value.strftime("%Y-%m-%d") if value else ""

    def render_action(self, record):
        url = reverse("leases:lease_detail", args=[record.pk])
        return mark_safe(f'<a class="btn btn-sm btn-secondary" href="{url}">Detail</a>')

    class Meta(ExportableTable.Meta):
        model = Lease
        # Include 'sn' for S.No like your LeaseTable does
        fields = (
            "sn",
            "property",
            "unit",
            "monthly_payment",
            "status",
            "start_date",
            "end_date",
            "balance",
            "action",
        )
        sequence = fields
        attrs = {
            "class": "table table-sm table-striped table-hover align-middle",
            "thead": {"class": "table-light"},
            "th": {"class": "text-nowrap"},
            # keep one line per row (no wrapping)
            "td": {"class": "text-nowrap"},
        }

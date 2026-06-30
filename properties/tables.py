import django_tables2 as tables
from django.contrib.humanize.templatetags.humanize import intcomma
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html

from tenants.models import TenantInterestType

from .models import Property, Unit


class ExportableTable(tables.Table):
    sn = tables.TemplateColumn(
        verbose_name="S.N#",
        template_code="{{ row_counter|add:1 }}",
        orderable=False,
        attrs={"td": {"class": "text-center"}, "th": {"class": "text-center"}},
    )

    actions = tables.TemplateColumn(
        verbose_name="Actions",
        orderable=False,
        template_name="components/action_buttons.html",
        attrs={"td": {"class": "text-center"}, "th": {"class": "text-center"}},
    )

    class Meta:
        export_formats = ["csv", "xlsx", "pdf", "ods"]
        template_name = "django_tables2/bootstrap5.html"
        orderable = True
        attrs = {
            "class": "table table-striped table-bordered table-hover align-middle table-sm",
            "style": "width: 100%; table-layout: auto; font-size: 16px;",
        }

        # Add PDF-specific configuration
        pdf_export_attrs = {
            "format": "A4",
            "column_widths": None,  # Will be overridden in child classes
        }


class UnitTable(ExportableTable):
    unit_number = tables.Column(
        verbose_name="Unit",
        linkify=lambda record: reverse("properties:unit_detail", args=[record.pk]),
    )

    property = tables.Column(verbose_name="Property")
    interest_type = tables.Column(verbose_name="Building Type", empty_values=())

    monthly_rent = tables.Column(verbose_name="Rent")
    electric_meter_num = tables.Column(verbose_name="Electric Meter#")
    gas_meter_num = tables.Column(verbose_name="Gas Meter#")
    society_maintenance = tables.Column(verbose_name="Maintenance")
    water_charges = tables.Column(verbose_name="Water")
    security_requires = tables.Column(verbose_name="Security Deposit")
    status = tables.Column(
        verbose_name="Status",
        attrs={"td": {"class": "text-center"}, "th": {"class": "text-center"}},
    )
    show_publicly = tables.BooleanColumn(
        verbose_name="Public",
        yesno="Yes,No",
        attrs={
            "td": {"class": "text-center"},
            "th": {"class": "text-center"},
        },
    )

    actions = tables.TemplateColumn(
        verbose_name="Actions",
        orderable=False,
        template_name="properties/unit_actions.html",
        attrs={
            "td": {"class": "text-center unit-actions-cell"},
            "th": {"class": "text-center unit-actions-cell"},
        },
    )

    def __init__(self, *args, **kwargs):
        lead_interest_types = kwargs.pop("lead_interest_types", None)
        super().__init__(*args, **kwargs)
        self.lead_interest_types = (
            list(lead_interest_types)
            if lead_interest_types is not None
            else list(
                TenantInterestType.objects.filter(is_active=True).order_by(
                    "sort_order", "name"
                )
            )
        )
        self.default_interest_types = {
            item.code: item
            for item in self.lead_interest_types
            if item.code in {"single_room_attached_bath_kitchen", "two_room_flat"}
        }

    def _format_decimal(self, value):
        if value is None:
            return ""
        return intcomma(int(value)) if value == int(value) else intcomma(value)

    def _inline_text(self, record, field, display_value):
        return format_html(
            '<span class="unit-inline-edit" data-unit-id="{}" data-field="{}" '
            'data-value="{}" tabindex="0">{}</span>',
            record.pk,
            field,
            "" if display_value is None else display_value,
            display_value or "-",
        )

    def render_monthly_rent(self, value, record):
        return self._inline_text(record, "monthly_rent", self._format_decimal(value))

    def render_water_charges(self, value, record):
        return self._inline_text(record, "water_charges", self._format_decimal(value))

    def render_society_maintenance(self, value, record):
        return self._inline_text(
            record, "society_maintenance", self._format_decimal(value)
        )

    def render_security_requires(self, value, record):
        return self._inline_text(record, "security_requires", value or "")

    def value_monthly_rent(self, value, record):
        return self._format_decimal(value)

    def value_water_charges(self, value, record):
        return self._format_decimal(value)

    def value_society_maintenance(self, value, record):
        return self._format_decimal(value)

    def value_security_requires(self, value, record):
        return value or ""

    def render_interest_type(self, value, record):
        selected_id = record.interest_type_id or self._default_interest_type_id(record)
        selected_name = ""
        for interest_type in self.lead_interest_types:
            if selected_id == interest_type.pk:
                selected_name = interest_type.name
                break
        return format_html(
            '<span class="unit-building-type-edit" data-unit-id="{}" '
            'data-current-value="{}" tabindex="0">{}</span>',
            record.pk,
            selected_id or "",
            selected_name or "-",
        )

    def value_interest_type(self, value, record):
        if record.interest_type:
            return record.interest_type.name
        default_interest_type = self._default_interest_type(record)
        return default_interest_type.name if default_interest_type else ""

    def _default_interest_type(self, record):
        property_name = (
            record.property.property_name if record.property else ""
        ).lower()
        if "f56" in property_name and "basement" in property_name:
            return self.default_interest_types.get("single_room_attached_bath_kitchen")
        return self.default_interest_types.get("two_room_flat")

    def _default_interest_type_id(self, record):
        default_interest_type = self._default_interest_type(record)
        return default_interest_type.pk if default_interest_type else None

    def render_status(self, value, record):
        if getattr(record, "has_ending_soon_lease_history", False) or getattr(
            record, "has_ending_soon_lease", False
        ):
            end_date = getattr(
                record, "active_lease_history_end_date", None
            ) or getattr(record, "active_lease_end_date", None)

            date_text = end_date.strftime("%b %d, %Y") if end_date else ""

            badge = format_html(
                '<span class="badge bg-warning text-dark text-wrap" style="line-height:1.2;">'
                "<strong>Ending Soon</strong><br>"
                "<small>{}</small>"
                "</span>",
                date_text,
            )

            lease_id = getattr(
                record, "active_lease_history_lease_id", None
            ) or getattr(record, "active_lease_id", None)

            if lease_id:
                return format_html(
                    '<a href="{}" class="text-decoration-none">{}</a>',
                    reverse("leases:lease_detail", args=[lease_id]),
                    badge,
                )

            return badge

        if getattr(record, "has_active_lease_history", False) or getattr(
            record, "has_active_lease", False
        ):
            lease_id = getattr(
                record, "active_lease_history_lease_id", None
            ) or getattr(record, "active_lease_id", None)

            badge = format_html('<span class="badge bg-success">Occupied</span>')

            if lease_id:
                return format_html(
                    '<a href="{}" class="text-decoration-none">{}</a>',
                    reverse("leases:lease_detail", args=[lease_id]),
                    badge,
                )

            return badge

        if record.status == "maintenance":
            return format_html('<span class="badge bg-danger">Maintenance</span>')

        return format_html('<span class="badge bg-primary">Vacant</span>')

    def render_show_publicly(self, value, record):
        label = "Yes" if value else "No"
        css = "badge bg-success" if value else "badge bg-secondary"
        return format_html(
            '<span class="unit-public-edit {}" '
            'data-unit-id="{}" data-current-value="{}" tabindex="0">{}</span>',
            css,
            record.pk,
            "true" if value else "false",
            label,
        )

    def value_show_publicly(self, value, record):
        return "Yes" if value else "No"

    # Add PDF-specific column widths
    pdf_export_attrs = {
        **ExportableTable.Meta.pdf_export_attrs,
        "orientation": "landscape",
        "column_widths": {
            "sn": 40,  # Slightly wider for better visibility
            "unit_number": 70,
            "property": 70,  # More space for property names
            "interest_type": 60,
            "monthly_rent": 60,
            "electric_meter_num": 80,
            "gas_meter_num": 80,
            "society_maintenance": 80,
            "water_charges": 40,
            "security_requires": 80,
            "status": 50,
            # Note: 'actions' will be automatically excluded
        },
        "pdf_export_title": "Units Report",  # Custom title for PDF export
    }

    class Meta(ExportableTable.Meta):
        model = Unit
        fields = (
            "sn",
            "unit_number",
            "property",
            "interest_type",
            "monthly_rent",
            "electric_meter_num",
            "gas_meter_num",
            "society_maintenance",
            "water_charges",
            "security_requires",
            "status",
            "show_publicly",
            "actions",
        )
        sequence = fields
        order_by = "unit_number"


class PropertyTable(ExportableTable):
    property_name = tables.Column(
        verbose_name="Property Name", order_by="property_name"
    )
    owner_name = tables.Column(verbose_name="Owner", order_by="owner_name")
    owner_contact = tables.Column(accessor="owner_phone", verbose_name="Owner Contact")
    caretaker_name = tables.Column(verbose_name="Caretaker", order_by="caretaker_name")
    caretaker_contact = tables.Column(
        accessor="caretaker_phone", verbose_name="Caretaker Contact"
    )
    property_city = tables.Column(verbose_name="City")
    property_type = tables.Column(verbose_name="Type")

    total_units = tables.Column(
        verbose_name="Total Units",
        attrs={"td": {"class": "text-center"}, "th": {"class": "text-center"}},
    )
    pdf_export_title = "Properties Reports"

    created_at = tables.DateColumn(verbose_name="Created", format="Y-m-d")

    def render_actions(self, record):
        return render_to_string(
            "components/action_buttons.html",
            {
                "view_url": reverse("properties:property_detail", args=[record.pk]),
                "edit_url": reverse("properties:property_update", args=[record.pk]),
                "delete_url": reverse("properties:property_delete", args=[record.pk]),
            },
        )

    # Add PDF-specific column widths
    pdf_export_attrs = {
        **ExportableTable.Meta.pdf_export_attrs,
        "orientation": "landscape",
        "column_widths": {
            "sn": 40,
            "property_name": 80,
            "owner_name": 80,
            "owner_contact": 120,
            "caretaker_name": 80,
            "caretaker_contact": 80,
            "property_city": 80,
            "property_type": 70,
            "total_units": 50,
            "created_at": 60,
        },
    }

    class Meta(ExportableTable.Meta):
        model = Property
        fields = (
            "sn",
            "property_name",
            "owner_name",
            "owner_contact",
            "caretaker_name",
            "caretaker_contact",
            "property_city",
            "property_type",
            "total_units",
            "created_at",
            "actions",
        )
        sequence = fields
        order_by = "-created_at"

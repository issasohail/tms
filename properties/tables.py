import django_tables2 as tables
from django.contrib.humanize.templatetags.humanize import intcomma
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from core.utils.identity import format_phone

from .models import BuildingType, Property, Unit


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
    sn = tables.TemplateColumn(
        verbose_name="S.N#",
        template_code=(
            '{% widthratio table.page.number|add:"-1" 1 table.paginator.per_page '
            "as page_offset %}{{ row_counter|add:page_offset|add:1 }}"
        ),
        orderable=False,
        attrs={
            "td": {"class": "text-center unit-col sn-col"},
            "th": {"class": "text-center rotate-col"},
        },
    )

    unit_number = tables.Column(
        verbose_name="Unit",
        linkify=lambda record: reverse("properties:unit_detail", args=[record.pk]),
        attrs={"td": {"class": "unit-col unit-number-col"}, "th": {"class": "unit-col unit-number-col"}},
    )

    property = tables.Column(
        verbose_name="Property",
        attrs={"td": {"class": "unit-col property-col"}, "th": {"class": "unit-col property-col"}},
    )
    building_type = tables.Column(
        verbose_name="Building Type",
        empty_values=(),
        attrs={"td": {"class": "unit-col building-type-col"}, "th": {"class": "unit-col building-type-col"}},
    )

    monthly_rent = tables.Column(
        verbose_name="Monthly Rent",
        attrs={"td": {"class": "unit-col charge-col rent-col"}, "th": {"class": "unit-col charge-col rent-col rotate-col"}},
    )
    electric_meter_num = tables.Column(
        verbose_name="Electric Meter#",
        attrs={"td": {"class": "unit-col meter-col"}, "th": {"class": "unit-col meter-col rotate-col"}},
    )
    gas_meter_num = tables.Column(
        verbose_name="Gas Meter#",
        attrs={"td": {"class": "unit-col meter-col"}, "th": {"class": "unit-col meter-col rotate-col"}},
    )
    society_maintenance = tables.Column(
        verbose_name="Society Maintenance",
        attrs={"td": {"class": "unit-col charge-col maintenance-col"}, "th": {"class": "unit-col charge-col maintenance-col rotate-col"}},
    )
    water_charges = tables.Column(
        verbose_name="Water Charges",
        attrs={"td": {"class": "unit-col charge-col water-col"}, "th": {"class": "unit-col charge-col water-col rotate-col"}},
    )
    internet_charges = tables.Column(
        verbose_name="Internet Charges",
        attrs={"td": {"class": "unit-col charge-col internet-col"}, "th": {"class": "unit-col charge-col internet-col rotate-col"}},
    )
    security_requires = tables.Column(
        verbose_name="Security Text",
        attrs={"td": {"class": "unit-col charge-col security-text-col"}, "th": {"class": "unit-col charge-col security-text-col"}},
    )
    security_deposit_amount = tables.Column(
        verbose_name="Security Amount",
        attrs={"td": {"class": "unit-col charge-col security-amount-col"}, "th": {"class": "unit-col charge-col security-amount-col rotate-col"}},
    )
    room_amenities = tables.Column(
        verbose_name="Room Amenities",
        empty_values=(),
        orderable=False,
        attrs={
            "td": {"class": "unit-col room-amenities-col"},
            "th": {"class": "unit-col room-amenities-col rotate-col"},
        },
    )
    inventory = tables.Column(
        verbose_name="Inventory",
        empty_values=(),
        orderable=False,
        attrs={
            "td": {"class": "unit-col inventory-col"},
            "th": {"class": "unit-col inventory-col rotate-col"},
        },
    )
    status = tables.Column(
        verbose_name="Status",
        attrs={"td": {"class": "text-center unit-col status-col"}, "th": {"class": "text-center unit-col status-col"}},
    )
    show_publicly = tables.BooleanColumn(
        verbose_name="Public",
        yesno="Yes,No",
        attrs={
            "td": {"class": "text-center unit-col public-col"},
            "th": {"class": "text-center unit-col public-col rotate-col"},
        },
    )
    is_smart_meter = tables.BooleanColumn(
        verbose_name="Smart Meter",
        yesno="Yes,No",
        attrs={
            "td": {"class": "text-center unit-col smart-col"},
            "th": {"class": "text-center unit-col smart-col rotate-col"},
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
        building_types = kwargs.pop("building_types", None)
        self.inventory_definitions = list(kwargs.pop("inventory_definitions", []))
        super().__init__(*args, **kwargs)
        self.building_types = (
            list(building_types)
            if building_types is not None
            else list(
                BuildingType.objects.filter(is_active=True).order_by(
                    "sort_order", "name"
                )
            )
        )

    def _format_decimal(self, value):
        if value is None:
            return ""
        return intcomma(int(value)) if value == int(value) else intcomma(value)

    def _inline_text(self, record, field, display_value):
        return format_html(
            '<span class="unit-inline-edit" data-unit-id="{}" data-field="{}" '
            'data-property-id="{}" data-value="{}" tabindex="0" '
            'title="Click to edit">{}</span>',
            record.pk,
            field,
            record.property_id,
            "" if display_value is None else display_value,
            display_value or "-",
        )

    def _status_badge(self, record, value, label, css_class, extra_html=""):
        return format_html(
            '<span class="unit-status-edit badge {}" data-unit-id="{}" '
            'data-current-value="{}" tabindex="0" role="button" '
            'title="Click to change status">{}{}'
            '<i class="fas fa-chevron-down ms-1 unit-status-caret"></i></span>',
            css_class,
            record.pk,
            value,
            label,
            extra_html,
        )

    def render_monthly_rent(self, value, record):
        return self._inline_text(record, "monthly_rent", self._format_decimal(value))

    def render_water_charges(self, value, record):
        return self._inline_text(record, "water_charges", self._format_decimal(value))

    def render_internet_charges(self, value, record):
        return self._inline_text(record, "internet_charges", self._format_decimal(value))

    def render_society_maintenance(self, value, record):
        return self._inline_text(
            record, "society_maintenance", self._format_decimal(value)
        )

    def render_security_requires(self, value, record):
        return self._inline_text(record, "security_requires", value or "")

    def render_security_deposit_amount(self, value, record):
        return self._inline_text(
            record, "security_deposit_amount", self._format_decimal(value)
        )

    def value_monthly_rent(self, value, record):
        return self._format_decimal(value)

    def value_water_charges(self, value, record):
        return self._format_decimal(value)

    def value_internet_charges(self, value, record):
        return self._format_decimal(value)

    def value_society_maintenance(self, value, record):
        return self._format_decimal(value)

    def value_security_requires(self, value, record):
        return value or ""

    def value_security_deposit_amount(self, value, record):
        return self._format_decimal(value)

    def _room_amenity_rows(self, record):
        return [
            (record.bedrooms, "Bedroom"),
            (record.bathrooms, "Bathroom"),
            (record.kitchens, "Kitchen"),
            (record.hall, "Hall"),
            (record.wardrobes, "Wardrobe"),
        ]

    def render_room_amenities(self, record):
        rows = [
            (quantity, label)
            for quantity, label in self._room_amenity_rows(record)
            if quantity
        ]
        if not rows:
            return format_html('<span class="text-muted">None</span>')
        return format_html(
            '<div class="room-amenities-summary">{}</div>',
            format_html_join(
                "",
                '<span class="room-amenity-chip">{} {}</span>',
                (
                    (quantity, f"{label}s" if quantity != 1 else label)
                    for quantity, label in rows
                ),
            ),
        )

    def value_room_amenities(self, record):
        return ", ".join(
            f"{quantity} {label if quantity == 1 else label + 's'}"
            for quantity, label in self._room_amenity_rows(record)
            if quantity
        )

    def _effective_inventory_rows(self, record):
        values = {
            item.pk: {
                "item": item,
                "quantity": item.default_quantity,
                "is_included": item.is_active,
            }
            for item in self.inventory_definitions
        }
        for row in getattr(record.property, "unit_list_inventory", []):
            if row.item_id in values:
                values[row.item_id].update(
                    quantity=row.quantity, is_included=row.is_included
                )
        for row in getattr(record, "unit_list_inventory", []):
            if row.item_id in values:
                values[row.item_id].update(
                    quantity=row.quantity, is_included=row.is_included
                )
        return [
            row for row in values.values()
            if row["is_included"] and row["quantity"] > 0
        ]

    def render_inventory(self, record):
        rows = self._effective_inventory_rows(record)
        manage_url = reverse("leases:inventory_manage", args=["unit", record.pk])
        if not rows:
            return format_html(
                '<div class="inventory-cell-content"><span class="text-muted">None</span> '
                '<a class="inventory-manage-link" href="{}" title="Manage inventory">Manage</a></div>',
                manage_url,
            )
        visible_rows = rows[:4]
        summary = format_html_join(
            "",
            '<span class="inventory-chip">{} {}</span>',
            ((row["quantity"], row["item"].name) for row in visible_rows),
        )
        more = len(rows) - len(visible_rows)
        more_html = (
            format_html('<span class="inventory-more">+{} more</span>', more)
            if more
            else ""
        )
        full_text = ", ".join(
            f'{row["item"].name}: {row["quantity"]}' for row in rows
        )
        return format_html(
            '<div class="inventory-cell-content"><div class="inventory-summary" title="{}">{}{}</div>'
            '<a class="inventory-manage-link" href="{}">Manage</a></div>',
            full_text,
            summary,
            more_html,
            manage_url,
        )

    def value_inventory(self, record):
        return ", ".join(
            f'{row["item"].name}: {row["quantity"]}'
            for row in self._effective_inventory_rows(record)
        )

    def render_building_type(self, value, record):
        selected_id = record.building_type_id
        selected_name = record.building_type.name if record.building_type else ""
        return format_html(
            '<span class="unit-building-type-edit" data-unit-id="{}" '
            'data-current-value="{}" tabindex="0">{}</span>',
            record.pk,
            selected_id or "",
            selected_name or "-",
        )

    def value_building_type(self, value, record):
        return record.building_type.name if record.building_type else ""

    def render_status(self, value, record):
        if getattr(record, "has_ending_soon_lease_history", False) or getattr(
            record, "has_ending_soon_lease", False
        ):
            end_date = getattr(
                record, "active_lease_history_end_date", None
            ) or getattr(record, "active_lease_end_date", None)
            date_text = end_date.strftime("%b %d, %Y") if end_date else ""
            lease_id = getattr(
                record, "active_lease_history_lease_id", None
            ) or getattr(record, "active_lease_id", None)
            badge = self._status_badge(
                record,
                "occupied",
                format_html("<strong>Ending Soon</strong>"),
                "bg-warning text-dark text-wrap",
                format_html("<br><small>{}</small>", date_text),
            )
            if lease_id:
                return format_html(
                    '<a href="{}" class="text-decoration-none unit-status-lease-link">{}</a>',
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

            badge = self._status_badge(
                record,
                "occupied",
                "Occupied",
                "bg-success",
            )

            if lease_id:
                return format_html(
                    '<a href="{}" class="text-decoration-none unit-status-lease-link">{}</a>',
                    reverse("leases:lease_detail", args=[lease_id]),
                    badge,
                )

            return badge

        if record.status == "maintenance":
            return self._status_badge(
                record,
                "maintenance",
                "Maintenance",
                "bg-danger",
            )

        if record.status == "occupied":
            return self._status_badge(
                record,
                "occupied",
                "Occupied",
                "bg-success",
            )

        return self._status_badge(
            record,
            "vacant",
            "Vacant",
            "bg-primary",
        )

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

    def render_is_smart_meter(self, value, record):
        label = "Yes" if value else "No"
        css = "badge bg-success" if value else "badge bg-secondary"
        return format_html(
            '<span class="unit-smart-edit {}" '
            'data-unit-id="{}" data-current-value="{}" tabindex="0">{}</span>',
            css,
            record.pk,
            "true" if value else "false",
            label,
        )

    def value_is_smart_meter(self, value, record):
        return "Yes" if value else "No"

    # Add PDF-specific column widths
    pdf_export_attrs = {
        **ExportableTable.Meta.pdf_export_attrs,
        "orientation": "landscape",
        "column_widths": {
            "sn": 40,  # Slightly wider for better visibility
            "unit_number": 70,
            "property": 70,  # More space for property names
            "building_type": 60,
            "monthly_rent": 60,
            "electric_meter_num": 80,
            "gas_meter_num": 80,
            "society_maintenance": 80,
            "water_charges": 40,
            "internet_charges": 55,
            "security_requires": 80,
            "security_deposit_amount": 70,
            "room_amenities": 110,
            "inventory": 140,
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
            "building_type",
            "monthly_rent",
            "electric_meter_num",
            "gas_meter_num",
            "society_maintenance",
            "water_charges",
            "internet_charges",
            "security_requires",
            "security_deposit_amount",
            "room_amenities",
            "inventory",
            "status",
            "is_smart_meter",
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

    def render_owner_contact(self, value):
        return format_phone(value)

    def render_caretaker_contact(self, value):
        return format_phone(value)

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

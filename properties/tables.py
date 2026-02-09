import django_tables2 as tables
from .models import Unit, Property
from django.urls import reverse
from django.template.loader import render_to_string
from django.contrib.humanize.templatetags.humanize import intcomma
from utils.pdf_export import handle_export


class ExportableTable(tables.Table):
    sn = tables.TemplateColumn(
        verbose_name="S.N#",
        template_code="{{ row_counter|add:1 }}",
        orderable=False,
        attrs={
            "td": {"class": "text-center"},
            "th": {"class": "text-center"}
        }
    )

    actions = tables.TemplateColumn(
        verbose_name="Actions",
        orderable=False,
        template_name="components/action_buttons.html",
        attrs={
            "td": {"class": "text-center"},
            "th": {"class": "text-center"}
        }

    )

    class Meta:
        export_formats = ['csv', 'xlsx', 'pdf', 'ods']
        template_name = "django_tables2/bootstrap5.html"
        orderable = True
        attrs = {
            'class': 'table table-striped table-bordered table-hover align-middle table-sm',
            'style': 'width: 100%; table-layout: auto; font-size: 16px;'
        }

     # Add PDF-specific configuration
        pdf_export_attrs = {
            'format': 'A4',
            'column_widths': None  # Will be overridden in child classes
        }


class UnitTable(ExportableTable):
    unit_number = tables.Column(
        verbose_name='Unit',
        linkify=lambda record: reverse(
            'properties:unit_detail', args=[record.pk])
    )

    property = tables.Column(verbose_name='Property')

    monthly_rent = tables.Column(verbose_name='Rent')
    electric_meter_num = tables.Column(verbose_name='Electric Meter#')
    gas_meter_num = tables.Column(verbose_name='Gas Meter#')
    society_maintenance = tables.Column(verbose_name='Maintenance')
    water_charges = tables.Column(verbose_name='Water')
    security_requires = tables.Column(verbose_name='Security Deposit')
    status = tables.Column(verbose_name='Status', attrs={
                           "td": {"class": "text-center"}, "th": {"class": "text-center"}})

    actions = tables.TemplateColumn(
        verbose_name='Actions',
        orderable=False,
        template_name='components/action_buttons.html',

    )

    def _format_decimal(self, value):
        if value is None:
            return ''
        return intcomma(int(value)) if value == int(value) else intcomma(value)

    def render_monthly_rent(self, value): return self._format_decimal(value)
    def render_water_charges(self, value): return self._format_decimal(value)

    def render_society_maintenance(
        self, value): return self._format_decimal(value)

    def render_actions(self, record):
        return render_to_string('components/action_buttons.html', {
            'view_url': reverse('properties:unit_detail', args=[record.pk]),
            'edit_url': reverse('properties:unit_update', args=[record.pk]),
            'delete_url': reverse('properties:unit_delete', args=[record.pk]),
            'extra_action_label': 'Custom',
            'extra_action_icon': 'fas fa-cog',
            'extra_action_color': 'primary'
        })

    # Add PDF-specific column widths
    pdf_export_attrs = {
        **ExportableTable.Meta.pdf_export_attrs,
        'orientation': 'landscape',
        'column_widths': {
            'sn': 40,  # Slightly wider for better visibility
            'unit_number': 70,
            'property': 70,  # More space for property names
            'monthly_rent': 60,
            'electric_meter_num': 80,
            'gas_meter_num': 80,
            'society_maintenance': 80,
            'water_charges': 40,
            'security_requires': 80,
            'status': 50,
            # Note: 'actions' will be automatically excluded
        },
        'pdf_export_title': 'Units Report'  # Custom title for PDF export
    }

    class Meta(ExportableTable.Meta):
        model = Unit
        fields = (
            'sn', 'unit_number', 'property', 'monthly_rent',
            'electric_meter_num', 'gas_meter_num', 'society_maintenance',
            'water_charges', 'security_requires', 'status', 'actions'
        )
        sequence = fields
        order_by = 'unit_number'


class PropertyTable(ExportableTable):
    property_name = tables.Column(
        verbose_name='Property Name', order_by='property_name')
    owner_name = tables.Column(verbose_name='Owner', order_by='owner_name')
    owner_contact = tables.Column(
        accessor='owner_phone', verbose_name='Owner Contact')
    caretaker_name = tables.Column(
        verbose_name='Caretaker', order_by='caretaker_name')
    caretaker_contact = tables.Column(
        accessor='caretaker_phone', verbose_name='Caretaker Contact')
    property_city = tables.Column(verbose_name='City')
    property_type = tables.Column(verbose_name='Type')

    total_units = tables.Column(
        verbose_name='Total Units',
        attrs={
            "td": {"class": "text-center"},
            "th": {"class": "text-center"}
        }
    )
    pdf_export_title = "Properties Reports"

    created_at = tables.DateColumn(verbose_name='Created', format='Y-m-d')

    def render_actions(self, record):
        return render_to_string('components/action_buttons.html', {
            'view_url': reverse('properties:property_detail', args=[record.pk]),
            'edit_url': reverse('properties:property_update', args=[record.pk]),
            'delete_url': reverse('properties:property_delete', args=[record.pk]),
        })

    # Add PDF-specific column widths
    pdf_export_attrs = {
        **ExportableTable.Meta.pdf_export_attrs,
        'orientation': 'landscape',
        'column_widths': {
            'sn': 40,
            'property_name': 80,
            'owner_name': 80,
            'owner_contact': 120,
            'caretaker_name': 80,
            'caretaker_contact': 80,
            'property_city': 80,
            'property_type': 70,
            'total_units': 50,
            'created_at': 60,
        }
    }

    class Meta(ExportableTable.Meta):
        model = Property
        fields = (
            'sn', 'property_name', 'owner_name', 'owner_contact',
            'caretaker_name', 'caretaker_contact', 'property_city',
            'property_type', 'total_units', 'created_at', 'actions'
        )
        sequence = fields
        order_by = '-created_at'

import django_tables2 as tables
from .models import Utility
from django_tables2.utils import A
from django.urls import reverse
from django.template.loader import render_to_string
from django.contrib.humanize.templatetags.humanize import intcomma
from utils.pdf_export import handle_export


class UtilityTable(tables.Table):
    sn = tables.Column(verbose_name='S.N#', empty_values=(), orderable=False)

    property = tables.Column(
        accessor='property.property_name', verbose_name='Property')
    utility_type = tables.Column(verbose_name='Type')
    amount = tables.Column(verbose_name='Amount')
    billing_date = tables.DateColumn(verbose_name='Billing Date')
    due_date = tables.DateColumn(verbose_name='Due Date')
    distribution_method = tables.Column(verbose_name='Distribution')

    actions = tables.TemplateColumn(
        template_name='components/action_buttons.html',
        verbose_name='Actions',
        orderable=False,
        extra_context={
            'view_name': 'utilities:utility_detail',
            'edit_name': 'utilities:utility_update',
            'delete_name': 'utilities:utility_delete',
            'custom_action_name': 'utilities:utility_distribute',
            'extra_action_label': 'Distribute',
            'extra_action_icon': 'fas fa-share',
            'extra_action_color': 'success'
        },
        attrs={"td": {"class": "text-nowrap"}}
    )

    def render_sn(self):
        self.counter = getattr(self, 'counter', 0) + 1
        return self.counter

    def render_amount(self, value):
        return f"{value:,.2f}"

    def render_utility_type(self, record):
        return record.get_utility_type_display()

    def render_distribution_method(self, record):
        return record.get_distribution_method_display()

    def render_billing_date(self, value):
        """Format date for exports"""
        return value.strftime('%Y-%m-%d') if value else ""

    def render_due_date(self, value):
        """Format date for exports"""
        return value.strftime('%Y-%m-%d') if value else ""

    def render_actions(self, record):
        return render_to_string('components/action_buttons.html', {
            'view_url': reverse('utilities:utility_detail', args=[record.pk]),
            'edit_url': reverse('utilities:utility_update', args=[record.pk]),
            'delete_url': reverse('utilities:utility_delete', args=[record.pk]),


        })
    pdf_export_attrs = {
        'orientation': 'landscape',
        'column_widths': {
            'sn': 40,
            'property': 100,
            'utility_type': 80,
            'amount': 70,
            'billing_date': 80,
            'due_date': 80,
            'distribution_method': 100,
        },
        'pdf_export_title': 'Utilities Report'
    }

    class Meta:
        model = Utility
        template_name = 'django_tables2/bootstrap5.html'
        fields = (
            'sn', 'property', 'utility_type', 'amount',
            'billing_date', 'due_date', 'distribution_method'
        )
        attrs = {'class': 'table table-striped table-hover'}
        export_formats = ['csv', 'xlsx', 'pdf']  # Add supported export formats

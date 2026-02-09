import django_tables2 as tables
from django_tables2.utils import A
from django.utils.html import format_html
from django.urls import reverse
from .models import Tenant
from django.template.loader import render_to_string
from properties.tables import ExportableTable


class TenantTable(ExportableTable):
    # Serial number column
    sno = tables.Column(
        verbose_name='S.No',
        empty_values=(),
        orderable=False,
        attrs={
            'td': {'class': 'text-center'}
        }
    )

    full_name = tables.Column(
        linkify=('tenants:tenant_detail', [A('pk')]),
        accessor='get_full_name',
        verbose_name='Name',
        order_by=('first_name', 'last_name')
    )

    current_property = tables.Column(
        accessor='property_name',
        verbose_name='Property'
    )

    current_unit = tables.Column(
        accessor='unit_number',
        verbose_name='Unit'
    )

    # Action buttons column
    actions = tables.Column(
        verbose_name='Actions',
        empty_values=(),
        orderable=False,
        attrs={
            'td': {'class': 'text-center'}
        }
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.counter = 0

    def render_sno(self, record):
        """Render serial number based on row counter"""
        self.counter += 1
        if hasattr(self, 'page') and self.page:
            return self.page.start_index() + self.counter - 1
        return self.counter

    def render_actions(self, record):
        payment_url = reverse('payments:payment_create') + \
            f'?tenant_id={record.pk}'
        ledger_url = reverse('tenants:lease_ledger', args=[record.pk])

        return render_to_string('components/action_buttons.html', {
            'view_url': reverse('tenants:tenant_detail', args=[record.pk]),
            'edit_url': reverse('tenants:tenant_update', args=[record.pk]),
            'delete_url': reverse('tenants:tenant_delete', args=[record.pk]),
            'extra_actions': [
                {
                    'url': payment_url,
                    'label': 'Make Payment',
                    'icon': 'fas fa-money-bill-wave',
                    'color': 'success'
                },
                {
                    'url': ledger_url,
                    'label': 'View Ledger',
                    'icon': 'fas fa-file-invoice-dollar',
                    'color': 'info'
                }
            ]
        })

    def order_sno(self, queryset, is_descending):
        """Custom ordering for serial number (not really sortable)"""
        return queryset, False

    class Meta:
        model = Tenant
        template_name = 'django_tables2/bootstrap5.html'
        fields = ('sno', 'full_name', 'email', 'phone',
                  'current_property', 'current_unit', 'status', 'actions')
        attrs = {
            'class': 'table table-striped table-hover',
            'thead': {'class': 'table-light'}
        }
        row_attrs = {
            'class': lambda record: 'table-success' if record.is_active else 'table-secondary'
        }

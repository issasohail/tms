# invoices/tables.py
from django.utils.html import format_html
from django.urls import reverse
from django.template.loader import render_to_string
import django_tables2 as tables
from django_tables2.columns import DateColumn, Column
from .models import Invoice
from properties.tables import ExportableTable
from utils.pdf_export import handle_export


class InvoiceTable(ExportableTable):
    sn = tables.Column(
        verbose_name='#',
        empty_values=(),
        orderable=False,
        attrs={"td": {"class": "text-center"}}
    )

    invoice_number = tables.Column(
        verbose_name='Invoice #',
        linkify=lambda record: reverse(
            'invoices:invoice_detail', args=[record.pk])
    )

    lease = tables.Column(
        accessor='lease',
        verbose_name='Lease',
        linkify=lambda record: reverse(
            'leases:lease_detail', args=[record.lease.pk])
    )

    issue_date = DateColumn(format="M d, Y", verbose_name='Issue Date')
    due_date = DateColumn(format="M d, Y", verbose_name='Due Date')

    description = tables.Column(
        attrs={
            "td": {"class": "text-center"},
            "th": {"class": "text-center"}
        })

    total_amount = Column(
        verbose_name='Amount',
        attrs={
            "td": {"class": "text-end"},
            "th": {"class": "text-end"}
        },
        localize=True
    )

    actions = tables.TemplateColumn(
        template_name='components/action_buttons.html',
        verbose_name='Actions',
        orderable=False,

        attrs={"td": {"class": "text-nowrap"}}
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._row_counter = 0

    def render_sn(self, record):
        """Alternative for absolute numbering across pages"""
        if not hasattr(self, '_object_list'):
            self._object_list = list(self.data)
        return self._object_list.index(record) + 1

    def before_render(self, request):
        """Reset counter at the start of each render"""
        self._row_counter = 0

    def render_total_amount(self, value):
        """Format amount for display (if not using localize=True)."""
        return f"Rs. {float(value):,.2f}" if value else "0.00"

    def render_status(self, value):
        """Add badge styling to status (safely escaped)."""
        status_classes = {
            'draft': 'badge bg-secondary',
            'sent': 'badge bg-primary',
            'paid': 'badge bg-success',
            'overdue': 'badge bg-danger',
            'cancelled': 'badge bg-warning text-dark'
        }
        return format_html(
            '<span class="{}">{}</span>',
            status_classes.get(value, "badge bg-light text-dark"),
            value
        )

    def render_actions(self, record):
        return render_to_string('components/action_buttons.html', {
            'view_url': reverse('invoices:invoice_detail', args=[record.pk]),
            'edit_url': reverse('invoices:invoice_update', args=[record.pk]),
            'delete_url': reverse('invoices:invoice_delete', args=[record.pk]),


        })

    pdf_export_attrs = {
        'orientation': 'portrait',
        'column_widths': {
            'sn': 30,
            'invoice_number': 60,
            'lease': 100,
            'issue_date': 60,
            'due_date': 60,
            'status': 50,
            'total_amount': 60,
        },
        'pdf_export_title': 'Invoices Report'
    }

    class Meta(ExportableTable.Meta):
        model = Invoice
        fields = (
            'sn', 'invoice_number', 'lease', 'issue_date',
            'due_date', 'status', 'total_amount', 'actions'
        )
        sequence = fields
        order_by = '-issue_date'
        export_formats = ['csv', 'xlsx', 'pdf']


class InvoiceListView(tables.SingleTableView):
    model = Invoice
    table_class = InvoiceTable
    template_name = 'invoices/invoice_list.html'

    def get(self, request, *args, **kwargs):
        resp = handle_export(request, self.get_table(), 'invoices')
        return resp or super().get(request, *args, **kwargs)

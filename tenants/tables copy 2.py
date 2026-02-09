import django_tables2 as tables
from django_tables2.utils import A
from django.utils.html import format_html
from django.urls import reverse
from .models import Tenant
from django.template.loader import render_to_string
from properties.tables import ExportableTable
from django.utils.safestring import mark_safe
from django_tables2 import Table, Column
from django.utils.html import format_html
from django.urls import reverse
from payments.models import Payment  # Add this import
from invoices.models import Invoice  # Add this if you need to work with invoices
from decimal import Decimal
from django.utils.safestring import mark_safe


class TenantTable(ExportableTable):
    # Serial number column
    sn = tables.Column(
        verbose_name='S.N #',
        empty_values=(),
        orderable=False,
        attrs={
            # Reduced from original
            "td": {"class": "text-left", "style": "width: 40px;"},
            "th": {"style": "width: 40px;"}
        }
    )

    full_name = tables.LinkColumn(
        'tenants:tenant_detail',
        args=[tables.A('pk')],
        verbose_name='Name',
        accessor='get_full_name',
        order_by=('first_name', 'last_name'),
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 100px;"},
            "th": {"class": "text-truncate", "style": "max-width: 100px;"}
        }
    )

    email = tables.Column(
        verbose_name='Email',
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 80px;"},
            "th": {"class": "text-truncate", "style": "max-width: 80px;"}
        }
    )

    phone = tables.Column(
        verbose_name='Phone',
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 80px;"},
            "th": {"class": "text-truncate", "style": "max-width: 80px;"}
        }
    )

    property = tables.Column(
        verbose_name='Property',
        orderable=False,
        accessor='current_lease.unit.property.property_name',
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 80px;"},
            "th": {"class": "text-truncate", "style": "max-width: 80px;"}
        }
    )

    unit = tables.Column(
        verbose_name='Unit',
        orderable=False,
        accessor='current_lease.unit.unit_number',
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 100px;"},
            "th": {"class": "text-truncate", "style": "max-width: 100px;"}
        }
    )

    current_rent = tables.Column(
        verbose_name=mark_safe('Monthly\nPayment'),
        orderable=False,
        accessor='current_lease.monthly_rent',
        # Allow line break in header
        attrs={
            # Reduced width
            "td": {"class": "text-end", "style": "width: 60px;"},
            "th": {
                "class": "text-end",
                "style": """
                    width: 60px;
                    white-space: normal;
                    word-break: break-word;
                    line-height: 1.2;
                """
            }
        }
    )

    balance = tables.Column(
        verbose_name='Balance',
        orderable=False,
        accessor='current_lease.get_balance',
        attrs={
            # Reduced width
            "td": {"class": "text-end", "style": "width: 75px;"},
            "th": {"class": "text-end", "style": "width: 75px;"}
        }
    )

    status = tables.Column(
        verbose_name='Status',
        attrs={
            "td": {"style": "width: 50px;"},
            "th": {"style": "width: 50px;"}
        }
    )

    actions = tables.TemplateColumn(
        template_name='components/action_buttons.html',
        verbose_name='Actions',
        orderable=False,
        # attrs={"td": {"class": "text-nowrap", "style": "width: 120px;"}}
        # Reduced from 120px
        attrs={
            "td": {
                "class": "p-0",
                "style": """
                    width: 120px;
                    min-width: 120px;
                    max-width: 120px;
                    overflow: hidden;
                    line-height: 1;
                    padding: 0 !important;
                """
            },
            "th": {
                "style": """
                    width: 120px;
                    min-width: 120px;
                    max-width: 120px;
                """
            }
        }
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.row_counter = 0

    def render_sn(self):
        """Auto-incrementing serial number"""
        self.row_counter += 1
        return self.row_counter

    def render_email(self, value):
        """Truncate long emails with ellipsis"""
        if value and len(value) > 20:
            return format_html('<span title="{}">{}</span>', value, value[:20] + '...')
        return value

    def render_property(self, value, record):
        """Render property with link"""
        if record.current_lease and record.current_lease.unit:
            url = reverse('properties:property_detail', args=[
                record.current_lease.unit.property.id])
            return format_html('<a href="{}">{}</a>', url, value)
        return "-"

    def render_unit(self, value, record):
        """Render unit with link"""
        if record.current_lease and record.current_lease.unit:
            url = reverse('properties:unit_detail', args=[
                record.current_lease.unit.id])
            return format_html('<a href="{}">{}</a>', url, value)
        return "-"

    def render_current_rent(self, value):
        """Format rent value"""
        return f"${float(value):,.2f}" if value else "-"

    def render_balance(self, value):
        """Format balance value"""
        return f"${float(value):,.2f}" if value else "0.00"

    def render_status(self, value, record):
        """Render status with badge"""
        if value == 'active':
            return mark_safe('<span class="badge bg-success">Active</span>')
        return mark_safe('<span class="badge bg-secondary">Inactive</span>')

    def render_actions(self, record):
        active_lease = None
        if hasattr(record, 'active_leases') and record.active_leases:
            active_lease = record.active_leases[0]

        context = {
            'record': record,
            'view_url': reverse('tenants:tenant_detail', args=[record.pk]),
            'edit_url': reverse('tenants:tenant_update', args=[record.pk]),
            'delete_url': reverse('tenants:tenant_delete', args=[record.pk]),
            'make_payment_url': reverse('payments:payment_create') + f'?lease={active_lease.pk}' if active_lease else None,
            'send_message_url': reverse('notifications:create') + f'?tenant_id={record.pk}',
            'view_ledger_url': reverse('tenants:lease_ledger', kwargs={'lease_id': record.pk}),
            'send_ledger_url': reverse('tenants:send_ledger', kwargs={'pk': record.pk}),
            'whatsapp_url': f"javascript:sendWhatsApp({record.pk})",
            'record': record,
            'sms_url': f"javascript:sendSMS({record.pk}, '{record.phone}', '{record.first_name}', '{active_lease.unit.property.property_name if active_lease else ''}', '{active_lease.unit.unit_number if active_lease else ''}', {active_lease.get_balance if active_lease else 0})",
            'small_buttons': True  # Add this flag for smaller buttons
        }

        return render_to_string('components/action_buttons.html', context)

    def before_render(self, request):
        """Set export title based on filters before rendering"""
        if request and hasattr(request, 'GET'):
            filters = request.GET
            title_parts = ["Tenant Records"]

            if filters.get('property'):
                try:
                    from properties.models import Property
                    prop = Property.objects.get(pk=filters['property'])
                    title_parts.append(f"at {prop.property_name}")
                except:
                    pass

            if filters.get('unit'):
                try:
                    from properties.models import Unit
                    unit = Unit.objects.get(pk=filters['unit'])
                    title_parts.append(f"(Unit {unit.unit_number})")
                except:
                    pass

            if filters.get('status'):
                title_parts.append(f"with status {filters['status']}")

            self.export_title = " ".join(title_parts)

    pdf_export_attrs = {
        'orientation': 'portrait',
        'column_widths': {
            'sn': 20,
            'full_name': 100,
            'email': 100,
            'phone': 80,
            'property': 80,
            'unit': 60,
            'current_rent': 60,
            'balance': 60,
            'status': 60,
            'actions': 120,
        },
        'pdf_export_title': 'Tenants Report',
    }

    class Meta(ExportableTable.Meta):
        model = Tenant
        template_name = 'django_tables2/bootstrap5-responsive.html'
        fields = (
            'sn', 'full_name', 'email', 'phone',
            'property', 'unit', 'current_rent',
            'balance', 'status', 'actions'
        )
        sequence = fields
        attrs = {
            # Changed to match TenantDetailTable
            'class': 'table table-bordered table-hover',
            'thead': {
                'class': 'thead-light sticky-top',  # Added sticky-top
                'style': 'top: 0; z-index: 1; background-color: white;'  # Added sticky header style
            },
            'style': """
                /* Grid styling similar to TenantDetailTable */
                table {
                    width: 100%;
                    table-layout: fixed;
                }
                
                /* Force Monthly Payment header to two lines */
                th[aria-label='Monthly Payment'] {
                    white-space: normal !important;
                    word-break: break-word !important;
                    line-height: 1.2 !important;
                    padding-top: 8px;
                    padding-bottom: 8px;
                }
                
                /* Action buttons styling */
                .action-buttons-container {
                    display: grid;
                    grid-template-columns: repeat(3, 1fr);
                    grid-auto-rows: 32px;
                    gap: 2px;
                    width: 100%;
                }
                
                /* First 3 buttons in row 1 */
                .action-buttons-container .btn:nth-child(1),
                .action-buttons-container .btn:nth-child(2),
                .action-buttons-container .btn:nth-child(3) {
                    grid-row: 1;
                }
                
                /* Next buttons in row 2 */
                .action-buttons-container .btn:nth-child(4),
                .action-buttons-container .btn:nth-child(5),
                .action-buttons-container .btn:nth-child(6) {
                    grid-row: 2;
                }
                
                /* Compact button styling */
                .action-buttons-container .btn {
                    padding: 0.1rem 0.2rem;
                    font-size: 0.7rem;
                    min-width: 0;
                    width: 100%;
                    height: 28px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                
                /* Ensure table rows don't expand */
                table tbody tr {
                    height: 56px;  /* Exactly 2 rows of buttons */
                }
                
                /* Text truncation for all cells */
                td {
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }
            """
        }
        export_formats = ['csv', 'xlsx', 'pdf']


class LedgerTable(tables.Table):
    line_number = tables.Column(
        verbose_name='#', orderable=False, empty_values=())
    date = tables.Column(verbose_name='Date', accessor='transaction_date')
    type = tables.Column(verbose_name='Type')
    description = tables.Column(verbose_name='Description')
    amount = tables.Column(verbose_name='Amount')
    balance = tables.Column(verbose_name='Balance', empty_values=())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.running_balance = Decimal('0.00')

    def render_line_number(self, value, record):
        return self.page.start_index() + record['index'] if hasattr(self, 'page') else record['index'] + 1

    def render_amount(self, value):
        numeric_value = Decimal(str(value))
        formatted_value = "{:,.2f}".format(numeric_value)
        if numeric_value > 0:
            return format_html('<span class="text-success">${}</span>', formatted_value)
        return format_html('<span class="text-danger">-${}</span>', formatted_value)

    def render_balance(self, value, record):
        # Calculate running balance
        self.running_balance += Decimal(str(record['amount']))
        formatted_value = "{:,.2f}".format(self.running_balance)
        if self.running_balance >= 0:
            return format_html('<span class="text-success">${}</span>', formatted_value)
        return format_html('<span class="text-danger">-${}</span>', formatted_value)

    class Meta:
        attrs = {'class': 'table table-striped table-bordered'}
        export_formats = ['csv', 'xlsx', 'pdf']
        sequence = ('line_number', 'date', 'type',
                    'description', 'amount', 'balance')


# tables.py


class TenantDetailTable1(tables.Table):

    # Serial number column
    sn = tables.Column(
        verbose_name='S.N #',
        empty_values=(),
        orderable=False,
        attrs={"td": {"class": "text-left"}}
    )

    full_name = tables.LinkColumn(
        'tenants:tenant_detail',
        args=[tables.A('pk')],
        verbose_name='Name',
        accessor='get_full_name',
        order_by=('first_name', 'last_name')
    )

    email = tables.Column(
        verbose_name='Email',
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 150px;"},
            "th": {"class": "text-truncate", "style": "max-width: 150px;"}
        }
    )

    class Meta:
        model = Tenant
        template_name = "django_tables2/bootstrap4.html"
        fields = (
            'sn', 'fullname',  'email', 'phone', 'cnic',
            'address', 'gender', 'date_of_birth', 'emergency_contact_name',
            'emergency_contact_phone', 'emergency_contact_relation',
            'number_of_family_member', 'is_active', 'notes'
        )
        attrs = {
            'class': 'table table-bordered table-hover',
            'thead': {'class': 'thead-light'}
        }
    actions = tables.TemplateColumn(
        template_name='components/action_buttons.html',
        verbose_name='Actions',
        orderable=False,
        attrs={"td": {"class": "text-nowrap", "style": "max-width: 120px;"}}
    )
    photo_thumbnail = tables.TemplateColumn(
        template_name='tenants/photo_thumbnail_column.html',
        verbose_name='Photo',
        orderable=False,
        attrs={"td": {"class": "text-center"}}
    )

    cnic_front_thumbnail = tables.TemplateColumn(
        template_name='tenants/cnic_front_thumbnail_column.html',
        verbose_name='CNIC Front',
        orderable=False,
        extra_context={'field_name': 'cnic_front'},
        attrs={
            "td": {
                "class": "text-center",
                "style": "width: 120px; min-width: 120px;"  # Fixed width
            },
            "th": {
                "style": "width: 120px; min-width: 120px;"  # Matches td width
            }
        }
    )

    cnic_back_thumbnail = tables.TemplateColumn(
        template_name='tenants/cnic_back_thumbnail_column.html',
        verbose_name='CNIC Back',
        orderable=False,
        extra_context={'field_name': 'cnic_back'},
        attrs={
            "td": {
                "class": "text-center",
                "style": "width: 120px; min-width: 120px;"  # Fixed width
            },
            "th": {
                "style": "width: 120px; min-width: 120px;"  # Matches td width
            }
        }
    )

    notes = tables.Column(
        verbose_name='Notess',
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 150px;"},
            "th": {"class": "text-truncate", "style": "max-width: 150px;"}
        }
    )

    def __init__(self, *args, **kwargs):
        show_active_only = kwargs.pop('show_active_only', False)
        super().__init__(*args, **kwargs)

        # Update sequence to include new columns
        self.sequence = [
            'sn', 'photo_thumbnail', 'full_name', 'email', 'phone',
            'cnic_front_thumbnail', 'cnic_back_thumbnail'
        ]

        # Add remaining fields
        remaining_fields = [
            f for f in self.base_columns.keys() if f not in self.sequence]
        self.sequence.extend(remaining_fields)

        if show_active_only:
            self.sequence.insert(4, 'property')
            self.sequence.insert(5, 'unit')
            self.sequence.insert(6, 'balance')

    def render_sn(self, value):
        """Auto-incrementing serial number"""
        self.row_counter = getattr(self, 'row_counter', 0) + 1
        return self.row_counter

    class Meta:
        model = Tenant
        template_name = "django_tables2/bootstrap4-responsive.html"
        fields = (
            'sn', 'photo_thumbnail', 'full_name', 'email', 'phone',
            'cnic_front_thumbnail', 'cnic_back_thumbnail', 'cnic',
            'address', 'gender', 'date_of_birth', 'emergency_contact_name',
            'emergency_contact_phone', 'emergency_contact_relation',
            'number_of_family_member', 'is_active', 'notes'
        )
        attrs = {
            'class': 'table table-bordered table-hover',
            'thead': {
                'class': 'thead-light sticky-top',
                'style': 'top: 0; z-index: 1; background-color: white;'
            }
        }

    def render_first_name(self, value, record):
        return format_html(
            '<span class="editable-field" data-field="first_name" data-tenant-id="{}">{}</span>',
            record.id, value
        )

    # Add similar render methods for other editable fields

    def render_is_active(self, value):
        return format_html(
            '<span class="badge {}">{}</span>',
            'badge-active' if value else 'badge-inactive',
            'Active' if value else 'Inactive'
        )

    def render_date_of_birth(self, value):
        return value.strftime('%Y-%m-%d') if value else ''

    # Add property/unit/balance columns when showing active leases
    def __init__(self, *args, **kwargs):
        show_active_only = kwargs.pop('show_active_only', False)
        super().__init__(*args, **kwargs)

        if show_active_only:
            self.base_columns['property'] = tables.Column(
                accessor='current_lease.unit.property.property_name',
                verbose_name='Property'
            )
            self.base_columns['unit'] = tables.Column(
                accessor='current_lease.unit.unit_number',
                verbose_name='Unit'
            )
            self.base_columns['balance'] = tables.Column(
                accessor='balance',
                verbose_name='Balance'
            )
            self.sequence = (
                'sn', 'first_name', 'last_name', 'property', 'unit', 'balance',
                'email', 'phone', 'cnic', 'address', 'gender',
                'date_of_birth', 'emergency_contact_name',
                'emergency_contact_phone', 'emergency_contact_relation',
                'number_of_family_member', 'is_active', 'notes', 'actions'
            )

    def render_actions(self, record):
        active_lease = None
        if hasattr(record, 'active_leases') and record.active_leases:
            active_lease = record.active_leases[0]

        context = {
            'record': record,
            'view_url': reverse('tenants:tenant_detail', args=[record.pk]),
            'edit_url': reverse('tenants:tenant_update', args=[record.pk]),
            'delete_url': reverse('tenants:tenant_delete', args=[record.pk]),
            'make_payment_url': reverse('payments:payment_create') + f'?lease={active_lease.pk}' if active_lease else None,
            'send_message_url': reverse('notifications:create') + f'?tenant_id={record.pk}',
            'view_ledger_url': reverse('tenants:lease_ledger', kwargs={'lease_id': record.pk}),
            'send_ledger_url': reverse('tenants:send_ledger', kwargs={'pk': record.pk}),
            'whatsapp_url': f"javascript:sendWhatsApp({record.pk})",
            'record': record,
            'sms_url': f"javascript:sendSMS({record.pk}, '{record.phone}', '{record.first_name}', '{active_lease.unit.property.property_name if active_lease else ''}', '{active_lease.unit.unit_number if active_lease else ''}', {active_lease.get_balance if active_lease else 0})",
            'small_buttons': True  # Add this flag for smaller buttons
        }

        return render_to_string('components/action_buttons.html', context)


class TenantDetailTable(tables.Table):
    # Row 1 columns
    photo_thumbnail = tables.TemplateColumn(
        template_name='tenants/photo_thumbnail_column.html',
        verbose_name='Photo',
        orderable=False,
        attrs={
            "td": {"class": "text-center", "rowspan": "2", "style": "width: 80px;"},
            "th": {"style": "width: 80px;"}
        }
    )

    full_name = tables.LinkColumn(
        'tenants:tenant_detail',
        args=[tables.A('pk')],
        verbose_name='Name',
        accessor='get_full_name',
        order_by=('first_name', 'last_name'),
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 150px;"},
            "th": {"class": "text-truncate", "style": "max-width: 150px;"}
        }
    )

    email = tables.Column(
        verbose_name='Email',
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 150px;"},
            "th": {"class": "text-truncate", "style": "max-width: 150px;"}
        }
    )

    phone = tables.Column(
        verbose_name='Phone',
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 100px;"},
            "th": {"class": "text-truncate", "style": "max-width: 100px;"}
        }
    )

    cnic_front_thumbnail = tables.TemplateColumn(
        template_name='tenants/cnic_front_thumbnail_column.html',
        verbose_name='CNIC Front',
        orderable=False,
        attrs={
            "td": {"class": "text-center", "rowspan": "2", "style": "width: 120px;"},
            "th": {"style": "width: 120px;"}
        }
    )

    cnic_back_thumbnail = tables.TemplateColumn(
        template_name='tenants/cnic_back_thumbnail_column.html',
        verbose_name='CNIC Back',
        orderable=False,
        attrs={
            "td": {"class": "text-center", "rowspan": "2", "style": "width: 120px;"},
            "th": {"style": "width: 120px;"}
        }
    )

    cnic = tables.Column(
        verbose_name='CNIC',
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 100px;"},
            "th": {"class": "text-truncate", "style": "max-width: 100px;"}
        }
    )

    address = tables.Column(
        verbose_name='Address',
        attrs={
            "td": {"class": "text-truncate", "rowspan": "2", "style": "max-width: 200px;"},
            "th": {"class": "text-truncate", "style": "max-width: 200px;"}
        }
    )

    gender = tables.Column(
        verbose_name='Gender',
        attrs={
            "td": {"class": "text-center", "style": "width: 80px;"},
            "th": {"style": "width: 80px;"}
        }
    )

    notes = tables.Column(
        verbose_name='Notes',
        attrs={
            "td": {"class": "text-truncate", "rowspan": "2", "style": "max-width: 200px;"},
            "th": {"class": "text-truncate", "style": "max-width: 200px;"}
        }
    )

    actions = tables.TemplateColumn(
        template_name='components/action_buttons.html',
        verbose_name='Actions',
        orderable=False,
        attrs={
            "td": {"class": "text-center", "rowspan": "2", "style": "width: 120px;"},
            "th": {"style": "width: 120px;"}
        }
    )

    # Row 2 columns (these won't have headers)
    emergency_contact_name = tables.Column(
        verbose_name='Emergency Contact',
        accessor='emergency_contact_name',
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 150px;"},
            "th": {"class": "empty-header"}  # Hidden header
        }
    )

    emergency_contact_relation = tables.Column(
        verbose_name='Relation',
        accessor='emergency_contact_relation',
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 100px;"},
            "th": {"class": "empty-header"}  # Hidden header
        }
    )

    emergency_contact_phone = tables.Column(
        verbose_name='Contact Phone',
        accessor='emergency_contact_phone',
        attrs={
            "td": {"class": "text-truncate", "style": "max-width: 100px;"},
            "th": {"class": "empty-header"}  # Hidden header
        }
    )

    number_of_family_member = tables.Column(
        verbose_name='Family Members',
        attrs={
            "td": {"class": "text-center", "style": "width: 80px;"},
            "th": {"class": "empty-header"}  # Hidden header
        }
    )

    class Meta:
        model = Tenant
        template_name = "django_tables2/bootstrap4-responsive.html"
        sequence = (
            'photo_thumbnail', 'full_name', 'email', 'phone',
            'cnic_front_thumbnail', 'cnic_back_thumbnail', 'cnic',
            'address', 'gender', 'notes', 'actions',
            'emergency_contact_name', 'emergency_contact_relation',
            'emergency_contact_phone', 'number_of_family_member'
        )
        attrs = {
            'class': 'table table-bordered table-hover',
            'thead': {
                'class': 'thead-light sticky-top',
                'style': 'top: 0; z-index: 1; background-color: white;'
            }
        }
        row_attrs = {
            'class': lambda record: 'merged-row' if record else ''
        }

    def __init__(self, *args, **kwargs):
        self.show_active_only = kwargs.pop('show_active_only', False)
        super().__init__(*args, **kwargs)

        if self.show_active_only:
            # Add property/unit/balance columns when showing active leases
            self.base_columns['property'] = tables.Column(
                accessor='current_lease.unit.property.property_name',
                verbose_name='Property'
            )
            self.base_columns['unit'] = tables.Column(
                accessor='current_lease.unit.unit_number',
                verbose_name='Unit'
            )
            self.base_columns['balance'] = tables.Column(
                accessor='balance',
                verbose_name='Balance'
            )

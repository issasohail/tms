# tables.py
import django_tables2 as tables
from django.core.cache import cache
from django_tables2.utils import A
from django.urls import reverse
from django.template.loader import render_to_string
from django.utils.html import format_html
from core.currency import format_money
from core.models import GlobalSettings
from .models import Expense, ExpenseDistribution


def _global_settings():
    settings_obj = cache.get("core.global_settings")
    if settings_obj is None:
        settings_obj = GlobalSettings.get_solo()
        cache.set("core.global_settings", settings_obj, 60)
    return settings_obj


class ExpenseTable(tables.Table):
    sn = tables.TemplateColumn(
        verbose_name="S.N#",
        template_code="{{ row_counter|add:1 }}",
        orderable=False,
        attrs={"td": {"class": "col-sn text-center"},
               "th": {"class": "col-sn text-center"}}
    )
    # --- Desktop/Tablet (≥ md) columns: show separately ---
    date = tables.DateColumn(
        verbose_name="Date",
        format="M d, Y",
        attrs={"td": {"class": "col-date d-none d-md-table-cell text-nowrap"},
               "th": {"class": "col-date d-none d-md-table-cell text-nowrap"}}
    )

    # Keep link behavior; we render custom HTML so we can add the Unit line on phones
    property = tables.LinkColumn(
        'properties:property_detail',
        args=[A('property.pk')],
        verbose_name="Property",
        attrs={"td": {"class": "col-property text-nowrap"},
               "th": {"class": "col-property text-nowrap"}}
    )

    # Hidden on < md; visible from md and up
    unit = tables.Column(
        accessor='unit.unit_number',
        verbose_name="Unit",
        attrs={"td": {"class": "col-unit d-none d-md-table-cell text-nowrap"},
               "th": {"class": "col-unit d-none d-md-table-cell text-nowrap"}}
    )

    # Hidden on < md; visible from md and up
    category = tables.Column(
        accessor='category.name',
        verbose_name="Category",
        attrs={"td": {"class": "col-category d-none d-md-table-cell text-nowrap"},
               "th": {"class": "col-category d-none d-md-table-cell text-nowrap"}}
    )

    # Visible on all sizes; we inject Category + tiny Description for phones
    description = tables.Column(
        verbose_name="Description",
        attrs={"td": {"class": "col-desc"},
               "th": {"class": "col-desc"}}
    )

    # Small-screen only: Date + Amount combined (Date first line, Amount second line)
    when = tables.Column(
        empty_values=(),
        verbose_name="Date / Amount",
        attrs={"td": {"class": "col-when d-md-none"},
               "th": {"class": "col-when d-md-none"}}
    )

    amount = tables.Column(
        verbose_name="Amount",
        attrs={"td": {"class": "col-amount d-none d-md-table-cell text-end text-nowrap"},
               "th": {"class": "col-amount d-none d-md-table-cell text-end text-nowrap"}}
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._global_settings = _global_settings()

    def render_when(self, record):
        # Date (line 1), Amount (line 2) for small screens
        date_str = record.date.strftime(
            '%Y-%m-%d') if getattr(record, 'date', None) else '—'
        try:
            amt = format_money(record.amount, self._global_settings)
        except (TypeError, ValueError):
            amt = format_money(0, self._global_settings)
        return format_html('<div class="when-date">{}</div><div class="when-amt">{}</div>', date_str, amt)

    def render_amount(self, value):
        try:
            return format_money(value, self._global_settings)
        except (TypeError, ValueError):
            return format_money(0, self._global_settings)

    receipt = tables.Column(
        empty_values=(),
        verbose_name="Receipt",
        attrs={"td": {"class": "col-receipt text-center"},
               "th": {"class": "col-receipt text-center"}}
    )

    def render_receipt(self, record):
        urls = []
        if hasattr(record, 'receipts'):
            urls = [r.image.url for r in record.receipts.all()
                    if getattr(r, 'image', None)]
        if not urls and getattr(record, 'receipt', None):
            urls = [record.receipt.url]
        if urls:
            return format_html(
                '<a href="#" class="view-receipt" data-urls="{}" '
                'aria-label="View receipt" title="View receipt">'
                '<i class="fas fa-receipt text-success" aria-hidden="true"></i></a>',
                "|".join(urls),
            )
        return format_html(
            '<span class="text-muted" aria-label="No receipt" title="No receipt">&mdash;</span>'
        )

    actions = tables.TemplateColumn(
        verbose_name='Actions',
        orderable=False,
        template_name='components/action_buttons.html',
        attrs={"td": {"class": "col-actions text-nowrap"},
               "th": {"class": "col-actions text-nowrap"}}
    )

    # ---------- Renderers for responsive content ----------
    def _unit_text(self, record):
        if record.unit_id:
            return getattr(record.unit, 'unit_number', '') or str(record.unit_id)
        dists = list(record.distributions.all()) if hasattr(
            record, 'distributions') else []
        total_units_mgr = getattr(
            getattr(record, 'property', None), 'unit_set', None)
        total_units = total_units_mgr.all().count() if total_units_mgr else 0
        if dists and len(dists) == total_units and total_units:
            return "All Unit"
        if len(dists) == 1:
            return getattr(dists[0].unit, 'unit_number', '') or str(dists[0].unit_id)
        if len(dists) > 1:
            return "Multiple"
        return "—"

    def render_property(self, record):
        prop = getattr(record, 'property', None)
        if prop:
            link = reverse('properties:property_detail', args=[prop.pk])
            main = prop.property_name
        else:
            link, main = "#", "—"
        unit_text = self._unit_text(record)
        # Add a second "Unit:" line for < md only
        return format_html(
            '<a href="{}">{}</a>'
            '<div class="prop-mobile-unit d-md-none">'
            '  <span class="rt-label"></span> <span class="rt-value">{}</span>'
            '</div>',
            link, main, unit_text
        )

    def render_description(self, record, value):
        cat = getattr(getattr(record, 'category', None),
                      'name', None) or '(Uncategorized)'
        desc = value or ''
        # Desktop/tablet (≥ md): plain description
        # Phone (< md): Category line (1) + Description line (2, tiny)
        return format_html(
            '<span class="d-none d-md-inline">{}</span>'
            '<div class="d-md-none">'
            '  <div class="desc-mobile-cat"><span class="rt-label"></span> '
            '    <span class="rt-cat">{}</span></div>'
            '  <div class="desc-mobile-desc">{}</div>'
            '</div>',
            desc, cat, desc
        )

    def _receipt_urls(self, record):  # <-- add self
        urls = []
        if hasattr(record, 'receipts'):
            urls = [
                r.image.url
                for r in record.receipts.all()
                if getattr(r, 'image', None)
            ]
        if not urls and getattr(record, 'receipt', None):
            urls = [record.receipt.url]
        return urls

    def render_actions(self, record):
        return render_to_string(
            'components/action_buttons.html',
            {
                'view_url':   reverse('expenses:expense_detail', args=[record.pk]),
                'edit_url':   reverse('expenses:expense_update', args=[record.pk]),
                'delete_url': reverse('expenses:expense_delete', args=[record.pk]),
            },
            request=getattr(self, "request", None),
        )

    class Meta:
        model = Expense
        template_name = 'django_tables2/bootstrap5.html'
        fields = (
            'sn', 'property', 'unit', 'category', 'date', 'when',
            'description', 'amount',  'receipt', 'actions'
        )
        attrs = {'class': 'table table-striped table-hover align-middle'}
        export_formats = ['csv', 'xlsx', 'pdf']


class ExpenseDistributionTable(tables.Table):
    id = tables.LinkColumn(
        'expenses:expense_detail',
        args=[A('expense.pk')],
        verbose_name="Expense ID"
    )

    unit = tables.LinkColumn(
        'tenants:unit_detail',
        args=[A('unit.pk')],
        verbose_name="Unit"
    )

    included_in_invoice = tables.BooleanColumn(
        verbose_name='In Invoice?',
        yesno='✓,✗'
    )

    class Meta:
        model = ExpenseDistribution
        template_name = 'django_tables2/bootstrap5.html'
        fields = ('id', 'expense', 'unit', 'amount', 'included_in_invoice')
        attrs = {'class': 'table table-striped table-hover'}

# invoices/views.py
# adjust import if Category lives elsewhere
from .services import first_of_month, ensure_month_invoice, active_leases_qs
from django.views.decorators.http import require_GET, require_POST
from datetime import datetime, date
from .models import RecurringCharge  # IMPORTANT: we need the model here
from django.views.generic import CreateView, UpdateView
from .models import RecurringCharge  # safe to import models here
from django.urls import reverse  # ensure present near imports
from django.apps import apps  # (ensure this import exists at the top)
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import ListView, CreateView, UpdateView
from .models import RecurringCharge  # OK to import here (not in models.py)
from django.views.generic import TemplateView
from calendar import monthrange
from datetime import date
from invoices.models import Invoice, InvoiceItem, RecurringCharge, WaterBill, ItemCategory
from .models import InvoiceItem, ItemCategory
from decimal import Decimal, InvalidOperation
from .models import Invoice  # adjust if Category is elsewhere
from django.db.models import Sum
from django.db.models import Prefetch
from django.views.generic import DetailView
from .models import Invoice
from decimal import Decimal
from .models import ItemCategory
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.shortcuts import redirect
from django.db.models import Count, ProtectedError
from .models import RecurringCharge
from datetime import date, timedelta
from django.utils import timezone
from properties.models import Property, Unit  # adjust imports

from .forms import InvoiceForm
from .models import Invoice, InvoiceItem, ItemCategory
from django.db import transaction
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.views.generic import ListView, CreateView, UpdateView, DetailView, DeleteView
from django.urls import reverse_lazy
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage
from django.http import HttpResponse
from django.template.loader import render_to_string
from weasyprint import HTML
from django.forms import inlineformset_factory
from django.utils.timezone import now
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.utils.html import escape
from django_tables2 import SingleTableView
from django.conf import settings
from django.views import View
from .models import Invoice, InvoiceItem
from .forms import InvoiceForm
from tenants.models import Tenant
from properties.models import Property
from .tables import InvoiceTable
import json
from django.contrib import messages
from django.db.models import Count
from django.http import JsonResponse, HttpResponseBadRequest, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from .models import ItemCategory
from .forms import InvoiceForm
import asyncio
from django.db.models import F, Value
from django.db.models.functions import Concat
from .aggregates import GroupConcat
from django.urls import reverse
# top of views.py
from .models import (
    Invoice, InvoiceItem, ItemCategory, RecurringCharge, WaterBill
)
from .forms import InvoiceForm
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from datetime import date, timedelta
from .services import run_monthly_billing_for, first_of_month
from django.apps import apps
from decimal import Decimal
from django.views.generic import TemplateView
from django.http import JsonResponse, HttpResponseBadRequest
from django.apps import apps
from django.utils.dateformat import format as dj_format
from django.db.models import Q


qs = (
    Invoice.objects
    .select_related('lease__tenant', 'lease__unit__property')
    .prefetch_related('items__category')
    .annotate(
        description_text=GroupConcat(
            Concat(F('items__category__name'), Value(
                ': '), F('items__description')),
            distinct=True, separator=', '
        )
    )
)
# Formset definition
InvoiceItemFormset = inlineformset_factory(
    Invoice,
    InvoiceItem,
    fields=["category",
            "description", "amount", "is_recurring"],
    extra=1,
    can_delete=True
)


class InvoiceListView(SingleTableView):
    model = Invoice
    table_class = InvoiceTable
    template_name = 'invoices/invoice_list.html'
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset().select_related(
            'lease', 'lease__tenant', 'lease__unit')

        # Filter by property
        property_id = self.request.GET.get('property')
        if property_id:
            queryset = queryset.filter(lease__unit__property_id=property_id)

        # Filter by lease
        lease_id = self.request.GET.get('lease')
        if lease_id:
            queryset = queryset.filter(lease_id=lease_id)

        # Filter by status
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)

        # Filter by date range
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        if start_date and end_date:
            queryset = queryset.filter(
                issue_date__range=[start_date, end_date])

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'all_properties': Property.objects.all(),
            'status_choices': Invoice.INVOICE_STATUS,
            'current_property': self.request.GET.get('property'),
            'current_lease': self.request.GET.get('lease'),
            'current_status': self.request.GET.get('status'),
        })
        return context


class InvoiceCreateView(LoginRequiredMixin, CreateView):
    model = Invoice
    form_class = InvoiceForm
    template_name = 'invoices/invoice_form.html'
    success_url = reverse_lazy('invoices:invoice_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # formset as before
        ctx['items'] = InvoiceItemFormset(
            self.request.POST) if self.request.POST else InvoiceItemFormset()
        # add active categories for datalist suggestions
        ctx['categories'] = ItemCategory.objects.filter(
            is_active=True).order_by('name').values('id', 'name')
        return ctx

    def form_valid(self, form):
        ctx = self.get_context_data()
        items = ctx['items']
        if not items.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        # save invoice, then items
        response = super().form_valid(form)
        items.instance = self.object
        items.save()

        # Expand invoice.description from items (Category: description, ...)
        parts = []
        for it in self.object.items.select_related('category'):
            nm = it.category.name if it.category_id else ''
            ds = (it.description or '').strip()
            if nm and ds:
                parts.append(f"{nm}: {ds}")
            elif nm:
                parts.append(nm)
            elif ds:
                parts.append(ds)
        self.object.description = ", ".join(parts)
        self.object.save(update_fields=['description'])

        return response

    def get_success_url(self):
        return reverse('invoices:invoice_detail', kwargs={'pk': self.object.pk})


class InvoiceUpdateView(LoginRequiredMixin, UpdateView):
    model = Invoice
    form_class = InvoiceForm
    template_name = 'invoices/invoice_form.html'
    success_url = reverse_lazy('invoices:invoice_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['items'] = InvoiceItemFormset(
            self.request.POST, instance=self.object) if self.request.POST else InvoiceItemFormset(instance=self.object)
        ctx['categories'] = ItemCategory.objects.filter(
            is_active=True).order_by('name').values('id', 'name')
        return ctx

    def form_valid(self, form):
        ctx = self.get_context_data()
        items = ctx['items']
        if not items.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        response = super().form_valid(form)
        items.instance = self.object
        items.save()

        parts = []
        for it in self.object.items.select_related('category'):
            nm = it.category.name if it.category_id else ''
            ds = (it.description or '').strip()
            if nm and ds:
                parts.append(f"{nm}: {ds}")
            elif nm:
                parts.append(nm)
            elif ds:
                parts.append(ds)
        self.object.description = ", ".join(parts)
        self.object.save(update_fields=['description'])

        return response

    def get_success_url(self):
        return reverse('invoices:invoice_detail', kwargs={'pk': self.object.pk})

# invoices/views.py

# invoices/views.py


class InvoiceDetailView(DetailView):
    model = Invoice
    template_name = "invoices/invoice_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        inv = self.object

        # Preload items + category
        items = inv.items.select_related('category').all()

        # Combined description = "Category: desc, Category: desc, ..."
        parts = []
        for it in items:
            if it.category and it.description:
                parts.append(f"{it.category.name}: {it.description}")
            elif it.category:
                parts.append(f"{it.category.name}")
            elif it.description:
                parts.append(it.description)
        ctx["combined_description"] = ", ".join(parts)

        # Compute total from items (don’t trust stale invoice.amount)
        ctx["computed_total"] = items.aggregate(t=Sum("amount"))["t"] or 0

        # For the category list box (datalist suggestions)
        from .models import ItemCategory
        ctx["categories"] = ItemCategory.objects.filter(
            is_active=True).order_by("name").values("id", "name")
        return ctx


class InvoiceDeleteView(LoginRequiredMixin, DeleteView):
    model = Invoice
    template_name = 'invoices/invoice_confirm_delete.html'
    success_url = reverse_lazy('invoices:invoice_list')

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Invoice deleted successfully.')
        return super().delete(request, *args, **kwargs)


@login_required
def generate_monthly_invoices(request):
    """
    Backward-compatible endpoint: generate next month’s invoices (and recurring).
    """
    today = date.today()
    y = today.year + (1 if today.month == 12 else 0)
    m = 1 if today.month == 12 else (today.month + 1)
    period = date(y, m, 1)

    # IMPORTANT: fix monthly_rent usage (it's a Lease field, not Unit)
    # We centralize generation in the service now:
    run_monthly_billing_for(period)

    messages.success(request, f"Invoices generated for {period:%B %Y}.")
    # adjust to your invoice list name
    return redirect('invoices:invoice_list')


def render_to_pdf(template_name, context):
    """Generate PDF from HTML template"""
    try:
        html_string = render_to_string(template_name, context)
        html = HTML(string=html_string)
        pdf_content = html.write_pdf()
        if not pdf_content:
            raise Exception("PDF generation returned empty content")
        return pdf_content
    except Exception as e:
        raise Exception(f"PDF generation failed: {str(e)}")


@login_required
def send_invoice_email(request, invoice_id):
    try:
        invoice = get_object_or_404(Invoice, pk=invoice_id)

        # Validate recipient email
        try:
            validate_email(invoice.lease.tenant.email)
        except ValidationError:
            messages.error(request, 'Invalid recipient email address.')
            return redirect('invoices:invoice_detail', pk=invoice_id)

        context = {
            'invoice': invoice,
            'items': invoice.items.all(),
            'date': now().date()
        }

        try:
            pdf_content = render_to_pdf('invoices/invoice_pdf.html', context)
        except Exception as e:
            messages.error(request, f'PDF generation failed: {str(e)}')
            return redirect('invoices:invoice_detail', pk=invoice_id)

        email = EmailMessage(
            subject=f'Invoice #{invoice.id}',
            body='Please find attached your invoice.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[invoice.lease.tenant.email],  # ✅ use lease.tenant
        )

        email.attach(f'invoice_{invoice.id}.pdf',
                     pdf_content, 'application/pdf')
        email.send()

        messages.success(request, 'Invoice email sent successfully!')
        return redirect('invoices:invoice_detail', pk=invoice_id)

    except Exception as e:
        messages.error(request, f'Failed to send email: {str(e)}')
        return redirect('invoices:invoice_detail', pk=invoice_id)


class InvoicePDFView(View):
    def get(self, request, pk):
        try:
            invoice = get_object_or_404(Invoice, pk=pk)
            pdf_content = render_to_pdf(
                'invoices/invoice_pdf.html', {'invoice': invoice})

            response = HttpResponse(
                pdf_content, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="Invoice_{invoice.invoice_number}.pdf"'
            return response
        except Exception as e:
            messages.error(request, f'Failed to generate PDF: {str(e)}')
            return redirect('invoices:invoice_detail', pk=pk)

# invoices/views.py


@login_required
def search_invoices_by_item_description(request):
    """
    Search invoices by item description
    """
    search_term = request.GET.get('q', '')

    invoices = (Invoice.objects
                .filter(items__description__icontains=search_term)
                .distinct()
                .select_related('lease', 'lease__tenant'))

    context = {
        'invoices': invoices,
        'search_term': search_term,
        'title': f'Search Results for "{search_term}"'
    }
    return render(request, 'invoices/invoice_search_results.html', context)


@login_required
@require_POST
def category_create_ajax(request):
    name = (request.POST.get('name') or '').strip()
    if not name:
        return HttpResponseBadRequest("Missing name")
    cat, created = ItemCategory.objects.get_or_create(
        name=name, defaults={'is_active': True})
    return JsonResponse({'id': cat.id, 'name': cat.name, 'created': created})


# views.py (pseudo)


def api_properties(request):
    data = list(Property.objects.values(
        'id', 'property_name').order_by('property_name'))
    return JsonResponse(data, safe=False)


def api_units(request):
    prop_id = request.GET.get('property_id')
    qs = Unit.objects.filter(property_id=prop_id).values(
        'id', 'unit_number').order_by('unit_number')
    return JsonResponse(list(qs), safe=False)


def api_leases(request):
    unit_id = request.GET.get('unit_id')
    qs = (Lease.objects
          # or however you mark active
          .filter(unit_id=unit_id, status='active')
          .select_related('tenant', 'unit')
          .order_by('id'))  # or start_date desc
    data = [{
        'id': l.id,
        'label': f"{l.tenant.first_name} {l.tenant.last_name} - {l.unit.unit_number} (Bal: Rs. {l.get_balance():,.2f})",
        'tenant': f"{l.tenant.first_name} {l.tenant.last_name}",
        'unit': l.unit.unit_number,
        'balance': float(l.get_balance()),
    } for l in qs]
    return JsonResponse(data, safe=False)


def api_tenants_for_unit(request):
    unit_id = request.GET.get('unit_id')
    qs = (Lease.objects
          .filter(unit_id=unit_id, status='active')
          .select_related('tenant', 'unit'))
    data = [{
        'lease_id': l.id,
        'tenant_id': l.tenant_id,
        'label': f"{l.tenant.first_name} {l.tenant.last_name} - {l.unit.unit_number}",
    } for l in qs]
    return JsonResponse(data, safe=False)

# views.py


def recurring_list(request):
    # filter by property/unit/lease like before (read GET params)
    qs = RecurringCharge.objects.select_related(
        'lease', 'category', 'lease__unit', 'lease__tenant')
    # apply property/unit/lease filters ...

    def next_run(rc):
        today = timezone.localdate()
        d = date(max(today.year, rc.start_date.year), max(
            today.month, rc.start_date.month), rc.day_of_month)
        if d < today:  # next month
            month = today.month + 1
            year = today.year + (1 if month > 12 else 0)
            month = 1 if month > 12 else month
            d = date(year, month, rc.day_of_month)
        return d
    rows = [{'rc': rc, 'next_run': next_run(rc)} for rc in qs]
    return render(request, 'invoices/recurring_list.html', {'rows': rows})


class CategoryListView(ListView):
    model = ItemCategory
    template_name = 'invoices/category_list.html'
    paginate_by = 20

    def get_queryset(self):
        qs = super().get_queryset().annotate(item_count=Count('invoiceitem'))
        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(name__icontains=q)

        sort = self.request.GET.get('sort', 'name')
        direction = self.request.GET.get('dir', 'asc')
        if sort not in {'name'}:
            sort = 'name'
        order = sort if direction == 'asc' else f'-{sort}'
        return qs.order_by(order, 'id')  # stable tiebreaker


class CategoryCreateView(CreateView):
    model = ItemCategory
    fields = ['name', 'is_active']
    template_name = 'invoices/category_form.html'
    success_url = reverse_lazy('invoices:category_list')


class CategoryUpdateView(UpdateView):
    model = ItemCategory
    fields = ['name', 'is_active']
    template_name = 'invoices/category_form.html'
    success_url = reverse_lazy('invoices:category_list')


class CategoryDeleteView(DeleteView):
    model = ItemCategory
    template_name = 'invoices/category_confirm_delete.html'
    success_url = reverse_lazy('invoices:category_list')

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        try:
            return super().post(request, *args, **kwargs)
        except ProtectedError:
            messages.error(
                request, "Cannot delete: this category is used by one or more invoice items.")
            return redirect('invoices:category_list')


@require_POST
def category_inline_update(request, pk):
    cat = get_object_or_404(ItemCategory, pk=pk)
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return HttpResponseBadRequest('Invalid JSON')

    name = (data.get('name') or '').strip()
    if not name:
        return JsonResponse({'ok': False, 'error': 'Name is required.'}, status=400)

    # enforce uniqueness (case-insensitive)
    if ItemCategory.objects.exclude(pk=cat.pk).filter(name__iexact=name).exists():
        return JsonResponse({'ok': False, 'error': 'A category with that name already exists.'}, status=400)

    cat.name = name
    cat.save(update_fields=['name'])
    return JsonResponse({'ok': True, 'id': cat.pk, 'name': cat.name})

# invoices/views.py


# invoices/views.py


@login_required
@require_POST
def invoice_items_bulk_update(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)

    try:
        payload = json.loads(request.body or "{}")
        items = payload.get("items", [])
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    for it in items:
        item_id = it.get("id")
        if not item_id:
            continue
        item = invoice.items.filter(pk=item_id).first()
        if not item:
            continue

        # Category is a required FK to ItemCategory — create it by name if missing
        cat_name = (it.get("category") or "").strip()
        if cat_name:
            cat, _ = ItemCategory.objects.get_or_create(name=cat_name)
            item.category = cat
        # If blank, leave current category as-is (don’t set None on a required FK)

        item.description = (it.get("description") or "").strip()

        try:
            item.amount = Decimal(str(it.get("amount") or "0"))
        except Exception:
            item.amount = Decimal("0")

        item.save()

    total = invoice.items.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    return JsonResponse({"ok": True, "invoice_total": str(total)})


async def export_invoices_csv(request):
    async def stream():
        yield "Number,Issue Date,Due Date,Amount,Status\n"
        qs = Invoice.objects.select_related(
            "lease").order_by("-issue_date")[:1000]
        for inv in qs:
            yield f'{inv.invoice_number},{inv.issue_date},{inv.due_date},{inv.amount},{inv.status}\n'
            await asyncio.sleep(0)  # let the event loop breathe
    return StreamingHttpResponse(stream(), content_type="text/csv")


@require_POST
def invoice_item_inline_update(request, pk):
    item = get_object_or_404(
        InvoiceItem.objects.select_related('invoice', 'category'), pk=pk)
    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return HttpResponseBadRequest('Invalid JSON')

    name = (payload.get('category_name') or '').strip()
    desc = (payload.get('description') or '').strip()
    amt = (payload.get('amount') or '').strip()

    if not name:
        return JsonResponse({'ok': False, 'error': 'Category is required.'}, status=400)
    try:
        amount = Decimal(amt)
        if amount < 0:
            raise InvalidOperation
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid amount.'}, status=400)

    # ensure category exists (create on the fly)
    cat, _ = ItemCategory.objects.get_or_create(
        name=name, defaults={'is_active': True})

    item.category = cat
    item.description = desc
    item.amount = amount
    item.save()

    # recompute invoice total (if you already have signals, this is optional)
    total = item.invoice.items.aggregate(t=Sum('amount'))[
        't'] or Decimal('0.00')

    return JsonResponse({
        'ok': True,
        'id': item.pk,
        'category_name': item.category.name,
        'description': item.description,
        'amount': str(item.amount),
        'invoice_total': str(total),
    })
# invoices/services.py (new file)


def first_of_month(d): return d.replace(day=1)


def ensure_month_invoice(lease, period_date):
    """Return the single invoice object for this lease & period (create if missing)."""
    defaults = {
        'due_date': period_date + timedelta(days=7),
        'description': f"Invoice for {period_date:%B %Y}",
        'amount': Decimal('0.00'),
    }
    inv, _ = Invoice.objects.get_or_create(
        lease=lease,
        issue_date=period_date,
        defaults=defaults
    )
    return inv


def active_leases_qs():
    """
    Return a queryset of active leases without importing the model at module import time.
    Works whether Lease lives in 'leases' or inside 'invoices'.
    """
    try:
        # preferred if you have a separate 'leases' app
        Lease = apps.get_model('leases', 'Lease')
    except LookupError:
        # fallback if Lease is defined in invoices.models
        Lease = apps.get_model('invoices', 'Lease')
    return Lease.objects.filter(status='active')


def last_of_month(d: date) -> date:
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def apply_fixed_recurring(period_date: date, cutoff_today: bool = False):
    """
    Apply all active FIXED RecurringCharge rows into invoices for the month of `period_date`.

    Rules:
    - Include rows that have started on/before the period's last day.
    - Exclude rows that ended before the period's first day.
    - If cutoff_today=True *and* this is the current month, exclude rows whose end_date < today.
    - For scope LEASE: only that lease. PROPERTY: all leases in that property. GLOBAL: all active leases.
    """
    from .models import RecurringCharge, InvoiceItem  # local import to avoid import cycles

    rules = (RecurringCharge.objects
             .filter(active=True, kind='FIXED')
             .select_related('lease', 'property', 'category'))

    period_first = first_of_month(period_date)
    period_last = last_of_month(period_date)
    today = date.today()
    is_current_month = (period_first.year ==
                        today.year and period_first.month == today.month)

    for rc in rules:
        # Must have started by end of the period
        if rc.start_date and rc.start_date > period_last:
            continue

        # Must not have ended before the beginning of the period (or today, if current month with cutoff)
        if rc.end_date:
            end_cut = today if (
                cutoff_today and is_current_month) else period_first
            if rc.end_date < end_cut:
                continue

        # Determine target leases
        if rc.scope == 'LEASE' and rc.lease_id:
            targets = active_leases_qs().filter(pk=rc.lease_id)
        elif rc.scope == 'PROPERTY' and rc.property_id:
            targets = active_leases_qs().filter(unit__property_id=rc.property_id)
        else:  # GLOBAL
            targets = active_leases_qs()

        # Post one item per target lease, idempotently
        for lease in targets:
            inv = ensure_month_invoice(
                lease, period_first)  # invoice date = 1st
            desc = rc.description or (
                rc.category.name if rc.category_id else "Recurring")
            amt = rc.amount or Decimal('0.00')
            InvoiceItem.objects.get_or_create(
                invoice=inv,
                category=rc.category,
                description=desc,
                defaults={'amount': amt, 'is_recurring': True},
            )


def post_water_bill(water_bill_id):
    """Split a water bill evenly across active leases in that property and month."""
    wb = WaterBill.objects.select_related('property').get(pk=water_bill_id)
    if wb.posted:
        return  # idempotent

    leases = list(active_leases_qs().filter(unit__property=wb.property))
    if not leases:
        wb.posted = True
        wb.save(update_fields=['posted'])
        return

    # equal split with cent/paisa-safe rounding
    n = len(leases)
    base = (wb.total_amount / n).quantize(Decimal('0.01'))
    remainder = (wb.total_amount - base * n)  # e.g. 0.01..0.04
    steps = int((remainder * 100).copy_abs())  # number of extra paisa
    adjustments = [Decimal('0.00')] * n
    for i in range(steps):
        adjustments[i] += Decimal('0.01') if remainder > 0 else Decimal('-0.01')

    # find or create the category
    water_cat, _ = ItemCategory.objects.get_or_create(name='Water Charges')

    for lease, adj in zip(leases, adjustments):
        inv = ensure_month_invoice(lease, wb.period)
        InvoiceItem.objects.create(
            invoice=inv,
            category=water_cat,
            description=wb.description or f"Water charges {wb.period:%b %Y}",
            amount=base + adj
        )

    wb.posted = True
    wb.save(update_fields=['posted'])


@transaction.atomic
def run_monthly_billing_for(period_date: date, cutoff_today: bool = False):
    """
    Generate invoices for the month of `period_date`.
    If cutoff_today=True and `period_date` is the *current* month,
    skip recurring rows whose end_date has already passed (end_date < today).
    """
    # 1) Ensure one invoice per active lease (invoice date = 1st of month)
    for lease in active_leases_qs():
        ensure_month_invoice(lease, first_of_month(period_date))

    # 2) Apply fixed recurring rows with optional "current-month cutoff" logic
    apply_fixed_recurring(period_date, cutoff_today=cutoff_today)

    # 3) Post any pending water bills for this period
    for wb in WaterBill.objects.filter(period=first_of_month(period_date), posted=False):
        post_water_bill(wb.id)

# invoices/views.py


class RecurringChargeListView(TemplateView):
    template_name = 'invoices/recurring_list.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date.today()

        def calc_next_run(rc):
            # honor start/end window and clamp to month length
            base = rc.start_date if rc.start_date > today else today
            day = rc.day_of_month
            last = monthrange(base.year, base.month)[1]
            this = base.replace(day=min(day, last))
            if this < today:
                y = this.year + (1 if this.month == 12 else 0)
                m = 1 if this.month == 12 else (this.month + 1)
                last = monthrange(y, m)[1]
                this = this.replace(year=y, month=m, day=min(day, last))
            if rc.end_date and this > rc.end_date:
                return None
            return this

        qs = RecurringCharge.objects.select_related('lease__tenant', 'lease__unit', 'property', 'category')\
                                    .order_by('-active', 'scope', 'start_date')
        ctx['rows'] = [{'rc': rc, 'next_run': calc_next_run(rc)} for rc in qs]
        return ctx


# --- Recurring charges ---

# --- Recurring charges ---


class RecurringChargeCreateView(CreateView):
    model = RecurringCharge
    template_name = 'invoices/recurring_form.html'
    # or 'invoices:recurring_list'
    success_url = reverse_lazy('invoices:recurring_wizard')

    def get_form_class(self):
        # lazy import to avoid early model eval
        from .forms import RecurringChargeForm
        return RecurringChargeForm


class RecurringChargeUpdateView(UpdateView):
    model = RecurringCharge
    template_name = 'invoices/recurring_form.html'
    # or 'invoices:recurring_list'
    success_url = reverse_lazy('invoices:recurring_wizard')

    def get_form_class(self):
        # lazy import to avoid early model eval
        from .forms import RecurringChargeForm
        return RecurringChargeForm

    def get_queryset(self):
        # optional, but nice for templates/forms
        return RecurringCharge.objects.select_related('lease', 'property', 'category')


class RecurringChargeDeleteView(DeleteView):
    model = RecurringCharge
    template_name = 'confirm_delete.html'
    success_url = reverse_lazy('invoices:recurring_list')


@login_required
def run_billing_current(request):
    """Generate invoices for the current month with end-date cutoff at 'today'."""
    period = first_of_month(date.today())
    run_monthly_billing_for(period, cutoff_today=True)
    messages.success(request, f"Monthly billing generated for {period:%B %Y}.")
    # or 'invoices:recurring_list' if you prefer
    return redirect('invoices:invoice_list')


def run_billing_now(request):
    """
    POST: run billing for a target month.
    If 'month' query arg is provided as YYYY-MM, use that month’s first day.
    Otherwise, default to the first day of next month.
    """
    today = date.today()
    target = request.GET.get('month')
    if target:
        y, m = target.split('-')
        period = date(int(y), int(m), 1)
    else:
        # next month
        y = today.year + (1 if today.month == 12 else 0)
        m = 1 if today.month == 12 else (today.month + 1)
        period = date(y, m, 1)

    run_monthly_billing_for(period)
    messages.success(request, f"Monthly billing prepared for {period:%B %Y}.")
    return redirect('invoices:recurring_list')


# --- Water bills ---


class WaterBillListView(ListView):
    model = WaterBill
    template_name = 'invoices/waterbill_list.html'
    context_object_name = 'bills'
    paginate_by = 50

    def get_queryset(self):
        return (WaterBill.objects
                .select_related('property')
                .order_by('-period', 'property__property_name'))


class WaterBillCreateView(CreateView):
    template_name = 'invoices/waterbill_form.html'
    success_url = reverse_lazy('invoices:waterbill_list')

    def get_form_class(self):
        from .forms import WaterBillForm  # lazy import!
        return WaterBillForm


class WaterBillUpdateView(UpdateView):
    template_name = 'invoices/waterbill_form.html'
    success_url = reverse_lazy('invoices:waterbill_list')

    def get_form_class(self):
        from .forms import WaterBillForm  # lazy import!
        return WaterBillForm

    def get_queryset(self):
        return WaterBill.objects.select_related('property')


def waterbill_post(request, pk):
    bill = get_object_or_404(WaterBill, pk=pk)
    if bill.posted:
        messages.info(request, "This water bill is already posted.")
    else:
        from .services import post_water_bill
        post_water_bill(bill.id)
        messages.success(request, "Water bill posted and split across leases.")
    return redirect('invoices:waterbill_list')


# at the top of the file (near other imports)
# ...
# --- Recurring wizard (compact JSON-backed UI) -------------------------------

# --- Recurring wizard --------------------------------------------------------


# --- Billing Preview & Confirm (current month) --------------------------------


def _Lease():
    try:
        return apps.get_model('leases', 'Lease')
    except LookupError:
        return apps.get_model('invoices', 'Lease')


def _get_period_invoice(Invoice, lease, period_first):
    """
    Find an existing invoice for a lease at period_first using common field names.
    Returns Invoice or None.
    """
    for field in ('invoice_date', 'date', 'period'):
        try:
            q = {'lease': lease, field: period_first}
            inv = Invoice.objects.filter(**q).first()
            if inv:
                return inv
        except Exception:
            continue
    return None


def _targets_for_rc(rc, period_first, cutoff_today, filters):
    """
    Yield active target leases for this recurring rule, obeying scope and filters,
    and obeying start/end/cutoff (current-month) rules. Does *not* write.
    """
    Lease = _Lease()
    from .models import Property

    # Date window
    from calendar import monthrange
    period_last = date(period_first.year, period_first.month,
                       monthrange(period_first.year, period_first.month)[1])
    today = date.today()
    is_current_month = (period_first.year ==
                        today.year and period_first.month == today.month)

    # Start must be on/before period last day
    if rc.start_date and rc.start_date > period_last:
        return []

    # End must not be before period start (or today if cutoff in current month)
    if rc.end_date:
        end_cut = today if (
            cutoff_today and is_current_month) else period_first
        if rc.end_date < end_cut:
            return []

    # Base targets by scope
    qs = active_leases_qs().select_related('unit', 'tenant')  # only active leases
    if rc.scope == 'LEASE' and rc.lease_id:
        qs = qs.filter(pk=rc.lease_id)
    elif rc.scope == 'PROPERTY' and rc.property_id:
        qs = qs.filter(unit__property_id=rc.property_id)
    else:
        pass  # GLOBAL -> all active leases

    # Apply user filters (optional)
    prop_id = filters.get('property_id')
    unit_id = filters.get('unit_id')
    tenant_id = filters.get('tenant_id')

    if prop_id and prop_id != 'all':
        try:
            qs = qs.filter(unit__property_id=int(prop_id))
        except ValueError:
            return []
    if unit_id:
        try:
            qs = qs.filter(unit_id=int(unit_id))
        except ValueError:
            return []
    if tenant_id:
        try:
            qs = qs.filter(tenant_id=int(tenant_id))
        except ValueError:
            return []

    return list(qs)


@require_GET
def api_billing_preview_current(request):
    """
    Preview what *would* be created for the current month.
    Query params (optional): property_id, unit_id, tenant_id
    Rules:
      - one invoice per lease (date = 1st of month)
      - items from RecurringCharge(kind='FIXED', active=1), honoring scope
      - skip rows with amount <= 0.00
      - if current month: end_date must be >= today (your "still within today" rule)
      - show which items would be skipped as duplicates if an identical item already exists
    """
    from .models import RecurringCharge, Invoice, InvoiceItem

    period_first = first_of_month(date.today())
    filters = {
        'property_id': request.GET.get('property_id') or 'all',
        'unit_id': request.GET.get('unit_id') or '',
        'tenant_id': request.GET.get('tenant_id') or '',
    }
    cutoff_today = True  # as requested for current month generation

    # Build preview grouped by lease
    leases_map = {}  # lease_id -> data
    grand_total = Decimal('0.00')
    total_items = 0
    will_skip_zero = 0
    will_skip_dupe = 0

    rules = (RecurringCharge.objects
             .filter(active=True, kind='FIXED')
             .select_related('lease', 'property', 'category'))

    for rc in rules:
        targets = _targets_for_rc(rc, period_first, cutoff_today, filters)
        if not targets:
            continue

        for lease in targets:
            # find existing invoice (if any) for dupe detection
            inv = _get_period_invoice(Invoice, lease, period_first)

            desc = rc.description or (
                rc.category.name if rc.category_id else "Recurring")
            amt = rc.amount or Decimal('0.00')
            is_zero = (amt <= 0)

            # Duplicate if an invoice exists AND there is already an item with same (category, description)
            is_dupe = False
            if inv:
                is_dupe = InvoiceItem.objects.filter(
                    invoice=inv, category=rc.category, description=desc
                ).exists()

            entry = leases_map.setdefault(lease.id, {
                'lease_id': lease.id,
                'unit_name': getattr(lease.unit, 'unit_number', None) or getattr(lease.unit, 'name', '') or '',
                'tenant_name': (" ".join(filter(None, [
                    getattr(lease.tenant, 'first_name', None),
                    getattr(lease.tenant, 'last_name', None),
                ])).strip() or getattr(lease.tenant, 'name', '') or '—'),
                'items': [],
                'total': Decimal('0.00'),
            })

            entry['items'].append({
                'rc_id': rc.id,
                'category': (rc.category.name if rc.category_id else '—'),
                'category_id': rc.category_id,
                'description': desc,
                'amount': f"{amt:.2f}",
                'is_zero': is_zero,
                'is_duplicate': is_dupe,
            })

            total_items += 1
            if is_zero:
                will_skip_zero += 1
            elif is_dupe:
                will_skip_dupe += 1
            else:
                entry['total'] += amt

    # finalize totals
    out = []
    sno = 1
    for lease_id, entry in sorted(leases_map.items(), key=lambda kv: (kv[1]['unit_name'].lower(), kv[1]['tenant_name'].lower())):
        entry['sno'] = sno
        sno += 1
        entry['invoice_date'] = period_first.isoformat()
        entry['total'] = f"{entry['total']:.2f}"
        grand_total += Decimal(entry['total'])
        out.append(entry)

    payload = {
        'period': period_first.isoformat(),
        'leases': out,
        'grand_total': f"{grand_total:.2f}",
        'counts': {
            'leases': len(out),
            'items': total_items,
            'skipped_zero_amount': will_skip_zero,
            'skipped_duplicates': will_skip_dupe,
            'billable_items': total_items - will_skip_zero - will_skip_dupe,
        }
    }
    return JsonResponse(payload)


@require_POST
@transaction.atomic
def api_billing_generate_current(request):
    """
    Confirm + generate for the current month. Uses the same rules as preview.
    This simply calls your monthly service with the cutoff rule.
    """
    from .services import run_monthly_billing_for
    period_first = first_of_month(date.today())
    run_monthly_billing_for(period_first, cutoff_today=True)
    return JsonResponse({'ok': True, 'period': period_first.isoformat()})


class RecurringWizardView(TemplateView):
    template_name = 'invoices/recurring_wizard.html'

    def get_context_data(self, **kwargs):
        from .models import Property, ItemCategory
        from .forms import RecurringChargeForm
        ctx = super().get_context_data(**kwargs)
        ctx['properties'] = Property.objects.all().order_by('property_name')
        ctx['categories'] = list(
            ItemCategory.objects.order_by('name').values('id', 'name'))
        ctx['form'] = RecurringChargeForm()
        return ctx


def api_recurring_list(request):
    """
    GET params:
      - property_id: int or 'all'
      - active: '1' (default) to restrict to active leases
      - unit_id: optional int -> show only this unit
      - tenant_id: optional int -> show only this tenant
    """
    from .models import RecurringCharge
    prop_id = request.GET.get('property_id', 'all')
    active_only = str(request.GET.get('active', '1')
                      ).lower() in ('1', 'true', 'yes', 'on')
    unit_id = request.GET.get('unit_id')
    tenant_id = request.GET.get('tenant_id')

    qs = (RecurringCharge.objects
          .select_related('lease', 'lease__unit', 'lease__unit__property', 'lease__tenant', 'category'))

    if prop_id not in (None, '', 'all'):
        try:
            qs = qs.filter(lease__unit__property_id=int(prop_id))
        except ValueError:
            return HttpResponseBadRequest("Invalid property_id")
    if unit_id:
        try:
            qs = qs.filter(lease__unit_id=int(unit_id))
        except ValueError:
            return HttpResponseBadRequest("Invalid unit_id")
    if tenant_id:
        try:
            qs = qs.filter(lease__tenant_id=int(tenant_id))
        except ValueError:
            return HttpResponseBadRequest("Invalid tenant_id")
    if active_only:
        qs = qs.filter(lease__status='active')

    def unit_label(rc):
        u = rc.lease.unit
        return getattr(u, 'unit_number', None) or getattr(u, 'name', '') or ''

    rows = []
    for idx, rc in enumerate(qs.order_by('lease__unit__unit_number', 'category__name', 'id'), start=1):
        tenant = rc.lease.tenant
        tenant_name = (" ".join(filter(None, [
            getattr(tenant, 'first_name', None),
            getattr(tenant, 'last_name', None),
        ])).strip() or getattr(tenant, 'name', '') or '—')
        rows.append({
            'sno': idx,
            'id': rc.id,
            'property_id': rc.lease.unit.property_id,
            'property_name': getattr(rc.lease.unit.property, 'property_name', ''),
            'lease_id': rc.lease_id,
            'unit_id': rc.lease.unit_id,
            'unit_name': unit_label(rc),
            'tenant_id': getattr(tenant, 'id', None),
            'tenant_name': tenant_name,
            'category_id': rc.category_id,
            'category': rc.category.name if rc.category_id else '—',
            'description': rc.description or '',
            'amount': f"{(rc.amount or Decimal('0.00')):.2f}",
            'day_of_month': rc.day_of_month,
            'start_date': rc.start_date.isoformat() if rc.start_date else '',
            'end_date': rc.end_date.isoformat() if rc.end_date else '',
            'lease_end_date': (rc.lease.end_date.isoformat() if getattr(rc.lease, 'end_date', None) else ''),
            'active': rc.active,
        })
    return JsonResponse({'results': rows})


def api_leases_filtered(request):
    """
    GET:
      property_id: int or 'all'
      show: 'active' (default) or 'all'
    Returns: [{id, label, unit_name, tenant_name, start_date, end_date, status}]
    """
    Lease = _Lease()
    prop_id = request.GET.get('property_id', 'all')
    show = request.GET.get('show') or 'active'  # 'active' | 'all'

    qs = Lease.objects.select_related('unit', 'tenant')
    if prop_id != 'all':
        try:
            pid = int(prop_id)
        except ValueError:
            return HttpResponseBadRequest("Invalid property_id")
        qs = qs.filter(unit__property_id=pid)

    if show != 'all':
        qs = qs.filter(status='active')

    def unit_label(l):
        return getattr(l.unit, 'unit_number', None) or getattr(l.unit, 'name', '') or ''

    leases = list(qs)
    leases.sort(key=lambda l: (unit_label(l) or '').lower())

    out = []
    for l in leases:
        tenant_full = " ".join(filter(None, [
            getattr(l.tenant, 'first_name', None),
            getattr(l.tenant, 'last_name', None),
        ])).strip() or getattr(l.tenant, 'name', '') or '—'
        out.append({
            'id': l.id,
            'label': f"{unit_label(l)} — {tenant_full}",  # Unit — Tenant
            'unit_name': unit_label(l),
            'tenant_name': tenant_full,
            'start_date': (l.start_date.isoformat() if getattr(l, 'start_date', None) else ''),
            'end_date': (l.end_date.isoformat() if getattr(l, 'end_date', None) else ''),
            'status': l.status,
        })
    return JsonResponse({'results': out})


def api_recurring_for_lease(request, lease_id: int):
    """
    Return recurring items strictly from RecurringCharge for a given lease.
    Output rows are suitable for inline editing (category_id, amount, day_of_month, dates, active, description).
    """
    from .models import RecurringCharge

    Lease = _Lease()
    try:
        Lease.objects.only('id').get(pk=lease_id)
    except Lease.DoesNotExist:
        return JsonResponse({'results': []})

    rows = []
    qs = (RecurringCharge.objects
          .filter(lease_id=lease_id)
          .select_related('category')
          .order_by('category__name', 'description'))
    for rc in qs:
        rows.append({
            'id': rc.id,
            'category_id': rc.category_id,
            'category': rc.category.name if rc.category_id else '—',
            'description': rc.description or '',
            'amount': f"{(rc.amount or Decimal('0.00')):.2f}",
            'day_of_month': rc.day_of_month,
            'start_date': rc.start_date.isoformat() if rc.start_date else '',
            'end_date': rc.end_date.isoformat() if rc.end_date else '',
            'active': rc.active,
        })
    return JsonResponse({'results': rows})


@require_POST
def api_recurring_update(request, pk: int):
    from .models import RecurringCharge, ItemCategory
    rc = get_object_or_404(RecurringCharge, pk=pk)
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    def to_decimal(v):
        if v in (None, ''):
            return Decimal('0.00')
        return Decimal(str(v))

    def to_date(v):
        if not v:
            return None
        return datetime.strptime(v, '%Y-%m-%d').date()

    if 'category_id' in data:
        cat_id = data.get('category_id') or None
        if cat_id and not ItemCategory.objects.filter(pk=cat_id).exists():
            return HttpResponseBadRequest("Invalid category_id")
        rc.category_id = cat_id

    for key in ('description',):
        if key in data:
            setattr(rc, key, data.get(key) or '')

    if 'amount' in data:
        rc.amount = to_decimal(data.get('amount'))
    if 'day_of_month' in data:
        try:
            rc.day_of_month = max(
                1, min(31, int(data.get('day_of_month') or 1)))
        except ValueError:
            return HttpResponseBadRequest("Invalid day_of_month")
    if 'start_date' in data:
        rc.start_date = to_date(data.get('start_date'))
    if 'end_date' in data:
        rc.end_date = to_date(data.get('end_date'))
    if 'active' in data:
        rc.active = bool(data.get('active'))

    rc.save()
    return JsonResponse({'ok': True})


@require_POST
def api_recurring_delete(request, pk: int):
    from .models import RecurringCharge
    rc = get_object_or_404(RecurringCharge, pk=pk)
    rc.delete()
    return JsonResponse({'ok': True})


@require_POST
def api_recurring_create(request):
    from .models import RecurringCharge, ItemCategory
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    lease_id = data.get('lease_id')
    if not lease_id:
        return HttpResponseBadRequest("lease_id is required")

    Lease = _Lease()
    try:
        lease = Lease.objects.get(pk=int(lease_id))
    except Exception:
        return HttpResponseBadRequest("Invalid lease_id")

    # optional/nullable inputs
    cat_id = data.get('category_id') or None
    if cat_id and not ItemCategory.objects.filter(pk=cat_id).exists():
        return HttpResponseBadRequest("Invalid category_id")

    def to_decimal(v):
        if v in (None, ''):
            return Decimal('0.00')
        return Decimal(str(v))

    def to_date(v):
        if not v:
            return None
        return datetime.strptime(v, '%Y-%m-%d').date()

    rc = RecurringCharge.objects.create(
        lease=lease,
        property=lease.unit.property,
        category_id=cat_id,
        description=data.get('description') or '',
        amount=to_decimal(data.get('amount')),
        day_of_month=int(data.get('day_of_month') or 1),
        start_date=to_date(data.get('start_date')),
        end_date=to_date(data.get('end_date')),
        active=bool(data.get('active', True)),
        scope='LEASE',   # consistent with wizard use
        kind='FIXED',    # consistent with wizard use
    )

    return JsonResponse({'ok': True, 'id': rc.id})


@require_POST
def api_recurring_backfill(request, pk: int):
    from .services import backfill_recurring_to_invoices
    try:
        posted = backfill_recurring_to_invoices(pk)
    except Exception as e:
        return HttpResponseBadRequest(str(e))
    return JsonResponse({'ok': True, 'posted_items': posted})


def api_units_for_property(request):
    """
    GET: property_id=all|<int>, active=1|0
    Returns units (id, label 'Unit — Tenant') that have (optionally active) leases.
    """
    Lease = _Lease()
    prop_id = request.GET.get('property_id', 'all')
    active_only = str(request.GET.get('active', '1')
                      ).lower() in ('1', 'true', 'yes', 'on')

    qs = Lease.objects.select_related('unit', 'tenant')
    if prop_id != 'all':
        try:
            qs = qs.filter(unit__property_id=int(prop_id))
        except ValueError:
            return HttpResponseBadRequest("Invalid property_id")
    if active_only:
        qs = qs.filter(status='active')

    def unit_label(l):
        return getattr(l.unit, 'unit_number', None) or getattr(l.unit, 'name', '') or ''

    out = []
    seen_units = set()
    for l in qs:
        uid = l.unit_id
        if uid in seen_units:
            continue
        seen_units.add(uid)
        tenant_full = (" ".join(filter(None, [
            getattr(l.tenant, 'first_name', None),
            getattr(l.tenant, 'last_name', None),
        ])).strip() or getattr(l.tenant, 'name', '') or '—')
        out.append({
            'id': uid,
            'label': f"{unit_label(l)} — {tenant_full}",
        })
    # sort by unit label
    out.sort(key=lambda r: (r['label'] or '').lower())
    return JsonResponse({'results': out})


def api_tenants_for_property(request):
    """
    GET: property_id=all|<int>, active=1|0
    Returns tenants (id, label 'Tenant — Unit') for leases in scope.
    """
    Lease = _Lease()
    prop_id = request.GET.get('property_id', 'all')
    active_only = str(request.GET.get('active', '1')
                      ).lower() in ('1', 'true', 'yes', 'on')

    qs = Lease.objects.select_related('unit', 'tenant')
    if prop_id != 'all':
        try:
            qs = qs.filter(unit__property_id=int(prop_id))
        except ValueError:
            return HttpResponseBadRequest("Invalid property_id")
    if active_only:
        qs = qs.filter(status='active')

    def unit_label(l):
        return getattr(l.unit, 'unit_number', None) or getattr(l.unit, 'name', '') or ''

    out = []
    for l in qs:
        tid = getattr(l.tenant, 'id', None)
        if tid is None:
            continue
        tenant_full = (" ".join(filter(None, [
            getattr(l.tenant, 'first_name', None),
            getattr(l.tenant, 'last_name', None),
        ])).strip() or getattr(l.tenant, 'name', '') or '—')
        out.append({
            'id': tid,
            'label': f"{tenant_full} — {unit_label(l)}",
        })
    # de-dupe by (id,label)
    unique = {(r['id'], r['label']): r for r in out}.values()
    out = sorted(unique, key=lambda r: (r['label'] or '').lower())
    return JsonResponse({'results': out})

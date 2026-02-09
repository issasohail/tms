from io import BytesIO
from django.core.files.base import ContentFile
import fitz  # PyMuPDF
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from .models import Expense, ExpenseReceipt
from django.utils.text import slugify
from django.shortcuts import redirect, render
from django.db import transaction
import os
from .models import ExpenseReceipt
from .models import ExpenseReceipt, Expense
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from .models import ExpenseReceipt  # make sure this exists
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django.template.loader import render_to_string
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.views.generic import CreateView, UpdateView
from django.shortcuts import render, redirect
from .forms import ExpenseForm
from .models import Expense
from django.views.generic import UpdateView
from .models import Expense, ExpenseCategory, ExpenseDistribution, ExpenseReceipt
from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django_tables2 import SingleTableView
from django.db.models import Sum
from tenants.models import Tenant
from properties.models import Property
from .models import Expense, ExpenseCategory, ExpenseDistribution
from .tables import ExpenseTable, ExpenseDistributionTable
from .forms import ExpenseForm, ExpenseDistributionForm
from invoices.models import Invoice
from datetime import date
from utils.pdf_export import handle_export
from django.contrib.auth.mixins import LoginRequiredMixin
from django_tables2.export.views import ExportMixin
from django.http import JsonResponse
from .models import ExpenseCategory
from django.views.decorators.csrf import csrf_exempt
import json
from datetime import timedelta


# add near your other imports
from django_tables2 import SingleTableView
from django.utils import timezone
from django.apps import apps
from django.db.models import Q
from django.utils.dateformat import format as dj_format
import json

from .models import Expense
from .tables import ExpenseTable  # your existing table
# If you reused invoice categories for expense:
from invoices.models import ItemCategory  # shared category model


class ExpenseListView(SingleTableView):
    model = Expense
    table_class = ExpenseTable
    template_name = "expenses/expense_list.html"
    paginate_by = 20

    # same helper shape as invoices list
    def _period_to_dates(self, period: str):
        if not period:
            return None, None
        today = timezone.localdate()
        monday = today - timezone.timedelta(days=today.weekday())
        sunday = monday + timezone.timedelta(days=6)
        if period == "today":
            return today, today
        if period == "yesterday":
            y = today - timezone.timedelta(days=1)
            return y, y
        if period == "this_week":
            return monday, sunday
        if period == "last_week":
            lm = monday - timezone.timedelta(days=7)
            return lm, lm + timezone.timedelta(days=6)
        if period == "this_month":
            start = today.replace(day=1)
            # end of month
            if start.month < 12:
                end = (start.replace(month=start.month+1, day=1) -
                       timezone.timedelta(days=1))
            else:
                end = (start.replace(year=start.year+1, month=1,
                       day=1) - timezone.timedelta(days=1))
            return start, end
        if period == "last_month":
            this_start = today.replace(day=1)
            last_end = this_start - timezone.timedelta(days=1)
            last_start = last_end.replace(day=1)
            return last_start, last_end
        if period == "this_year":
            from datetime import date
            return date(today.year, 1, 1), date(today.year, 12, 31)
        return None, None

    def get_queryset(self):
        qs = (Expense.objects
              .select_related("property", "category")
              .prefetch_related("receipts", "distributions__unit"))

        r = self.request
        prop = r.GET.get("property") or r.GET.get("property_id")
        unit = r.GET.get("unit") or r.GET.get("unit_id")
        category = r.GET.get("category") or r.GET.get("category_id")
        start = r.GET.get("start_date")
        end = r.GET.get("end_date")
        period = r.GET.get("period")

        if period and not (start or end):
            s, e = self._period_to_dates(period)
            if s:
                start = s.isoformat()
            if e:
                end = e.isoformat()

        if prop:
            qs = qs.filter(property_id=prop)

        if unit:
            # supports either a direct FK (if you added Expense.unit) OR via distributions
            qs = qs.filter(Q(distributions__unit_id=unit)
                           | Q(unit_id=unit)).distinct()

        if category:
            qs = qs.filter(category_id=category)

        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        Property = apps.get_model("properties", "Property")
        Unit = apps.get_model("properties", "Unit")

        # Properties → select options
        props = Property.objects.all().order_by("property_name")
        ctx["property_options"] = [
            {"id": p.id, "name": p.property_name} for p in props]

        # Units by property (simple label: Unit Number)
        by_prop = {}
        for u in Unit.objects.select_related("property").order_by("property__property_name", "unit_number"):
            by_prop.setdefault(u.property_id, []).append(
                {"id": u.id, "label": getattr(
                    u, "unit_number", "") or str(u.id)}
            )
        ctx["units_by_property_json"] = json.dumps(by_prop)

        # Categories (shared with invoices)
        cats = ItemCategory.objects.filter(is_active=True).order_by("name")
        ctx["category_options"] = [{"id": c.id, "name": c.name} for c in cats]

        return ctx

    def get(self, request, *args, **kwargs):
        # Handle export requests
        export_response = self.handle_export(request)
        if export_response:
            return export_response
        return super().get(request, *args, **kwargs)

    def handle_export(self, request):
        """Handle export functionality"""
        self.object_list = self.get_queryset()
        table = self.get_table()
        export_name = "expenses_list"
        return handle_export(request, table, export_name)


class ExpenseDetailView(LoginRequiredMixin, DetailView):
    model = Expense
    template_name = 'expenses/expense_detail.html'
    context_object_name = 'expense'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['distributions'] = self.object.distributions.all()
        return context


def category_list_api(request):
    term = request.GET.get('term', '')
    categories = ExpenseCategory.objects.filter(name__icontains=term)
    return JsonResponse({
        'results': [{'id': c.id, 'text': c.name} for c in categories]
    })


@csrf_exempt
def category_add_api(request):
    data = json.loads(request.body)
    category, created = ExpenseCategory.objects.get_or_create(
        name=data['name'])
    return JsonResponse({'id': category.id, 'name': category.name})


# --- add this import near others ---


# If you have a custom form, keep this import; otherwise use `fields = [...]` below

# If you created the multi-image model:
try:
    from .models import ExpenseReceipt
    HAS_RECEIPTS = True
except Exception:
    HAS_RECEIPTS = False

# --- imports at top ---


# If you have the multi-image receipts model:

# ---------- existing CBVs (keep yours, just add context) ----------
# expenses/views.py


def _filename_tokens_for_expense(expense):
    # Property token: letters/numbers only, no dashes/spaces; TitleCase for readability
    raw_prop = getattr(getattr(expense, "property", None),
                       "property_name", "") or "Property"
    prop_token = slugify(raw_prop).replace("-", "")
    prop_token = prop_token.title() if prop_token else "Property"

    # Date token from expense.date (fallback: today)
    dt = getattr(expense, "date", None) or timezone.now().date()
    date_token = dt.strftime("%m%d%Y")  # mmddyyyy

    return prop_token, date_token


def _save_receipts(request, expense):
    """
    Saves all uploaded files from <input name="receipts" multiple>
    as ExpenseReceipt rows with filenames:
      PropertyName_mmddyyyy-sss.ext
    where sss is a 3-digit per-expense serial (001, 002, …).
    """
    files = request.FILES.getlist("receipts")
    if not files:
        return

    prop_token, date_token = _filename_tokens_for_expense(expense)

    # Start serial after already-existing receipts for this expense
    start = ExpenseReceipt.objects.filter(expense=expense).count()

    # Create one ExpenseReceipt per uploaded file
    for idx, f in enumerate(files, start=1):
        serial = f"{start + idx:03d}"
        _, ext = os.path.splitext(f.name)
        # keep original if present; default to .jpg
        ext = (ext or ".jpg").lower()

        # Set the desired filename before saving the ImageField
        f.name = f"{prop_token}_{date_token}-{serial}{ext}"

        ExpenseReceipt.objects.create(expense=expense, image=f)


# expenses/views.py


def _pdf_first_page_to_jpg(file_obj, out_name):
    file_obj.seek(0)
    doc = fitz.open(stream=file_obj.read(), filetype="pdf")
    if doc.page_count == 0:
        return None
    pix = doc[0].get_pixmap(dpi=144)
    buf = BytesIO(pix.tobytes("png"))  # Pillow will convert PNG->JPEG later
    return ContentFile(buf.getvalue(), name=out_name)


def _save_receipts(request, expense):
    files = request.FILES.getlist("receipts")
    if not files:
        return
    prop_token, date_token = _filename_tokens_for_expense(expense)
    start = ExpenseReceipt.objects.filter(expense=expense).count()

    for idx, f in enumerate(files, start=1):
        serial = f"{start + idx:03d}"
        _, ext = os.path.splitext(f.name)
        ext = (ext or ".jpg").lower()
        ctype = getattr(f, "content_type", "") or ""

        # If it's a PDF, render page 1 to an image file before creating ExpenseReceipt
        if ctype == "application/pdf" or ext == ".pdf":
            out_name = f"{prop_token}_{date_token}-{serial}.jpg"
            preview = _pdf_first_page_to_jpg(f, out_name)
            if preview:
                ExpenseReceipt.objects.create(expense=expense, image=preview)
            # (Optionally also store the original PDF on Expense.receipt if empty)
            if not expense.receipt:
                expense.receipt = f
                expense.save(update_fields=["receipt"])
            continue

        # images: keep your existing naming
        f.name = f"{prop_token}_{date_token}-{serial}{ext}"
        ExpenseReceipt.objects.create(expense=expense, image=f)


class ExpenseCreateView(LoginRequiredMixin, CreateView):
    model = Expense
    form_class = ExpenseForm
    template_name = "expenses/expense_form.html"

    def post(self, request, *args, **kwargs):
        print("ExpenseCreate/UpdateView POST hit",
              request.POST.dict().keys(), request.FILES.getlist("receipts"))
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        Property = apps.get_model("properties", "Property")
        Unit = apps.get_model("properties", "Unit")
        by_prop = {}
        for u in Unit.objects.select_related("property").order_by("property__property_name", "unit_number"):
            by_prop.setdefault(u.property_id, []).append({
                "id": u.id,
                "label": getattr(u, "unit_number", "") or str(u.id)
            })
        ctx["units_by_property_json"] = json.dumps(by_prop)
        ctx["recent_expenses"] = Expense.objects.order_by("-date", "-pk")[:10]
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        self.object = form.save()
        # save all images (multiple allowed)

        _save_receipts(self.request, self.object)

        if getattr(self.request, "htmx", False):
            return render(
                self.request,
                "expenses/partials/recent_expenses.html",
                {"recent_expenses": Expense.objects.order_by(
                    "-date", "-pk")[:10]},
            )
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse_lazy("expenses:expense_detail", kwargs={"pk": self.object.pk})

    def form_invalid(self, form):
        print("Form invalid:", form.errors)
        return super().form_invalid(form)


class ExpenseUpdateView(LoginRequiredMixin, UpdateView):
    model = Expense
    form_class = ExpenseForm
    template_name = "expenses/expense_form.html"

    def post(self, request, *args, **kwargs):
        print("UpdateView POST hit", request.POST.dict().keys(),
              request.FILES.getlist("receipts"))
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        Property = apps.get_model("properties", "Property")
        Unit = apps.get_model("properties", "Unit")
        by_prop = {}
        for u in Unit.objects.select_related("property").order_by("property__property_name", "unit_number"):
            by_prop.setdefault(u.property_id, []).append({
                "id": u.id,
                "label": getattr(u, "unit_number", "") or str(u.id)
            })
        ctx["units_by_property_json"] = json.dumps(by_prop)
        ctx["recent_expenses"] = Expense.objects.order_by("-date", "-pk")[:10]
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        self.object = form.save()
        # append any newly uploaded receipts
        _save_receipts(self.request, self.object)

        print("Receipts saved:", ExpenseReceipt.objects.filter(
            expense=self.object).count())
        #
        # Optional: handle deletions if you add checkboxes named delete_receipts
        ids_to_delete = self.request.POST.getlist("delete_receipts")
        if ids_to_delete:
            ExpenseReceipt.objects.filter(
                expense=self.object, id__in=ids_to_delete).delete()

        if getattr(self.request, "htmx", False):
            return render(
                self.request,
                "expenses/partials/recent_expenses.html",
                {"recent_expenses": Expense.objects.order_by(
                    "-date", "-pk")[:10]},
            )
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        print("Form invalid:", form.errors)
        return super().form_invalid(form)

    def get_success_url(self):
        return reverse_lazy("expenses:expense_detail", kwargs={"pk": self.object.pk})

# ---------- HTMX partial renderer ----------


def _render_receipts_partial(expense: Expense, request: HttpRequest) -> HttpResponse:
    return render(request, "expenses/partials/receipt_grid.html", {"expense": expense})


# views.py


@login_required
@require_POST
def receipt_add(request, expense_id):
    expense = get_object_or_404(Expense, pk=expense_id)
    img = request.FILES.get('image')
    if img:
        ExpenseReceipt.objects.create(
            expense=expense,
            image=img,
            comment=request.POST.get('comment', '').strip()
        )
    # re-render the partial
    return render(request, 'expenses/partials/receipt_grid.html', {'expense': expense})


@login_required
@require_POST
def receipt_update(request, pk):
    r = get_object_or_404(ExpenseReceipt, pk=pk)
    if 'image' in request.FILES:
        r.image = request.FILES['image']
    if 'comment' in request.POST:
        r.comment = request.POST['comment'].strip()
    r.save()
    return render(request, 'expenses/partials/receipt_grid.html', {'expense': r.expense})


@login_required
@require_POST
def receipt_delete(request, pk):
    r = get_object_or_404(ExpenseReceipt, pk=pk)
    expense = r.expense
    r.delete()
    return render(request, 'expenses/partials/receipt_grid.html', {'expense': expense})


class ExpenseDeleteView(LoginRequiredMixin, DeleteView):
    model = Expense
    template_name = 'expenses/expense_confirm_delete.html'
    success_url = reverse_lazy('expenses:expense_list')

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Expense deleted successfully.')
        return super().delete(request, *args, **kwargs)


class ExpenseDistributionListView(LoginRequiredMixin, SingleTableView):
    model = ExpenseDistribution
    table_class = ExpenseDistributionTable
    template_name = 'expenses/distribution_list.html'
    context_object_name = 'distributions'

    def get_queryset(self):
        queryset = super().get_queryset()
        expense_id = self.request.GET.get('expense')
        if expense_id:
            queryset = queryset.filter(expense_id=expense_id)
        return queryset


def distribute_expense(request, pk):
    expense = get_object_or_404(Expense, pk=pk)

    if expense.is_distributed:
        messages.warning(request, 'This expense has already been distributed.')
        return redirect('expenses:expense_detail', pk=expense.pk)

    if request.method == 'POST':
        distribution_method = request.POST.get(
            'distribution_method')  # set first
        # then log it
        print(f"Distribution Method: {distribution_method}")

        # Delete any existing distributions for this expense
        ExpenseDistribution.objects.filter(expense=expense).delete()

        units = expense.property.units.filter(status='occupied')
        total_units = units.count()
        total_occupants = sum(unit.tenants.count() for unit in units)

        if distribution_method == 'equal' or distribution_method == 'by_units':
            if total_units > 0:
                amount_per_unit = expense.amount / total_units
                for unit in units:
                    ExpenseDistribution.objects.create(
                        expense=expense,
                        unit=unit,
                        amount=amount_per_unit
                    )

        elif distribution_method == 'by_occupants':
            if total_occupants > 0:
                for unit in units:
                    occupants = unit.tenants.count()
                    if occupants:
                        amount = (expense.amount / total_occupants) * occupants
                        ExpenseDistribution.objects.create(
                            expense=expense,
                            unit=unit,
                            amount=amount
                        )

        expense.distribution_method = distribution_method
        expense.is_distributed = True
        expense.save()

        messages.success(
            request, f'Expense distributed successfully using {distribution_method.replace("_", " ")} method.'
        )
        return redirect('expenses:expense_detail', pk=expense.pk)

    return render(request, 'expenses/expensedistribution_form.html', {
        'expense': expense,
    })


def add_to_invoices(request, pk):
    expense = get_object_or_404(Expense, pk=pk)

    if not expense.is_distributed:
        messages.warning(request, 'This expense has not been distributed yet.')
        return redirect('expense_detail', pk=expense.pk)

    if request.method == 'POST':
        today = date.today()
        next_month = today.replace(day=1) + timedelta(days=32)
        first_of_next_month = next_month.replace(day=1)

        distributions = expense.distributions.filter(included_in_invoice=False)
        invoices_created = 0

        for distribution in distributions:
            tenant = distribution.unit.tenants.first()  # Assuming one tenant per unit
            if tenant:
                invoice = Invoice.objects.create(
                    tenant=tenant,
                    issue_date=first_of_next_month,
                    due_date=first_of_next_month + timedelta(days=7),
                    amount=distribution.amount,
                    description=f"{expense.description} (Distribution)"
                )
                distribution.included_in_invoice = True
                distribution.save()
                invoices_created += 1

        messages.success(
            request, f'Successfully added {invoices_created} expense distributions to invoices.')
        return redirect('expense_detail', pk=expense.pk)

    return render(request, 'expenses/add_to_invoices_confirm.html', {
        'expense': expense,
        'distributions': expense.distributions.filter(included_in_invoice=False),
    })


class ExpenseDistributionCreateView(LoginRequiredMixin, CreateView):
    model = ExpenseDistribution
    form_class = ExpenseDistributionForm
    template_name = 'expenses/expensedistribution_form.html'

    def get_initial(self):
        initial = super().get_initial()
        initial['expense'] = self.kwargs.get('expense_id')
        return initial

    def get_success_url(self):
        return reverse_lazy('expenses:expense_detail', kwargs={'pk': self.object.expense.pk})

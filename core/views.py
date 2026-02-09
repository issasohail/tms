# core/views.py
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import FormView
from django.shortcuts import render
from django.utils import timezone
from datetime import timedelta
from django.db import models

from .forms import GlobalSettingsForm
from .models import GlobalSettings
from tenants.models import Tenant
from payments.models import Payment
from invoices.models import Invoice
from expenses.models import Expense
from properties.models import Property, Unit
from django.contrib.auth.decorators import login_required


@login_required
def dashboard(request):
    today = timezone.now().date()
    thirty_days_ago = today - timedelta(days=30)

    total_properties = Property.objects.count()
    total_units = Unit.objects.count()
    occupied_units = Unit.objects.filter(status='occupied').count()
    vacancy_rate = ((total_units - occupied_units) /
                    total_units * 100) if total_units > 0 else 0

    total_tenants = Tenant.objects.filter(is_active=True).count()

    total_rent = Invoice.objects.filter(
        description__contains='Monthly Rent',
        issue_date__range=[thirty_days_ago, today]
    ).aggregate(total=models.Sum('amount'))['total'] or 0

    total_payments = Payment.objects.filter(
        payment_date__range=[thirty_days_ago, today]
    ).aggregate(total=models.Sum('amount'))['total'] or 0

    total_expenses = Expense.objects.filter(
        date__range=[thirty_days_ago, today]
    ).aggregate(total=models.Sum('amount'))['total'] or 0

    context = {
        'total_properties': total_properties,
        'total_units': total_units,
        'occupied_units': occupied_units,
        'vacancy_rate': round(vacancy_rate, 2),
        'total_tenants': total_tenants,
        'total_rent': total_rent,
        'total_payments': total_payments,
        'total_expenses': total_expenses,
        'net_income': total_payments - total_expenses,
        'recent_payments': Payment.objects.order_by('-payment_date')[:5],
        'recent_invoices': Invoice.objects.order_by('-issue_date')[:5],
        'upcoming_invoices': Invoice.objects.filter(
            due_date__gte=today, status__in=['unpaid', 'partially_paid']
        ).order_by('due_date')[:5],
    }
    return render(request, 'dashboard.html', context)


class SettingsView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    # <— moved from tms_config/...
    template_name = "core/settings.html"
    form_class = GlobalSettingsForm
    success_url = reverse_lazy("core:settings")          # <— update namespace

    def test_func(self):
        return self.request.user.is_staff or self.request.user.is_superuser

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        # If you use django-solo, keep get_solo(); otherwise fallback to first-or-create:
        try:
            instance = GlobalSettings.get_solo()
        except AttributeError:
            instance, _ = GlobalSettings.objects.get_or_create(pk=1)
        kw["instance"] = instance
        return kw

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Settings saved.")
        return super().form_valid(form)
# core/views.py
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt  # if you prefer manual CSRF handling
from .models import PaymentMethod


@require_POST
def payment_method_quick_add(request):
    """
    Quick add a payment method.
    Expects 'name' in POST, returns JSON {id, name}.
    """
    name = (request.POST.get('name') or '').strip()
    if not name:
        return HttpResponseBadRequest("Missing name")

    code = slugify(name) or 'method'
    # ensure unique code
    base_code = code
    i = 1
    while PaymentMethod.objects.filter(code=code).exists():
        i += 1
        code = f"{base_code}-{i}"

    pm = PaymentMethod.objects.create(
        name=name,
        code=code,
        is_active=True,
        sort_order=50,  # default
    )
    return JsonResponse({
        "id": pm.id,
        "name": pm.name,
    })


@require_POST
def payment_method_quick_edit(request):
    """
    Quick edit the name of an existing payment method.
    Expects 'id' and 'name' in POST.
    """
    try:
        pm_id = int(request.POST.get('id'))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid id")

    name = (request.POST.get('name') or '').strip()
    if not name:
        return HttpResponseBadRequest("Missing name")

    try:
        pm = PaymentMethod.objects.get(pk=pm_id)
    except PaymentMethod.DoesNotExist:
        return HttpResponseBadRequest("Payment method not found")

    pm.name = name
    pm.save(update_fields=['name'])

    return JsonResponse({
        "id": pm.id,
        "name": pm.name,
    })
# core/views.py

from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from .models import PaymentMethod


def payment_method_get(request, pk):
    pm = get_object_or_404(PaymentMethod, pk=pk)
    return JsonResponse({
        "id": pm.id,
        "name": pm.name,
        "code": pm.code,
        "sort_order": pm.sort_order,
        "is_active": pm.is_active,
    })


@require_POST
def payment_method_toggle(request, pk):
    pm = get_object_or_404(PaymentMethod, pk=pk)
    pm.is_active = not pm.is_active
    pm.save(update_fields=["is_active"])
    return JsonResponse({"ok": True})


@require_POST
def payment_method_save(request):
    pm_id = request.POST.get("id")
    name = request.POST.get("name", "").strip()
    code = request.POST.get("code", "").strip() or slugify(name)
    sort_order = int(request.POST.get("sort_order", "50"))
    is_active = request.POST.get("is_active") == "1"

    if not name:
        return HttpResponseBadRequest("Name required")

    if pm_id:
        pm = get_object_or_404(PaymentMethod, pk=pm_id)
    else:
        pm = PaymentMethod()

    pm.name = name
    pm.code = code
    pm.sort_order = sort_order
    pm.is_active = is_active
    pm.save()

    return JsonResponse({"ok": True})

# core/views.py
from django.shortcuts import render, redirect
from django.contrib import messages

from .models import GlobalSettings, PaymentMethod
from .forms import GlobalSettingsForm
# core/views.py
from django.views.generic import FormView
from django.urls import reverse_lazy
from django.contrib import messages

from .models import GlobalSettings, PaymentMethod
from .forms import GlobalSettingsForm


class SettingsView(FormView):
    template_name = "core/settings.html"
    form_class = GlobalSettingsForm
    success_url = reverse_lazy("core:settings")

    def get_form_kwargs(self):
        """
        Use the singleton GlobalSettings instance.
        """
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = GlobalSettings.get_solo()
        return kwargs

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Settings saved.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        """
        Add payment_methods so settings.html can render the list.
        """
        ctx = super().get_context_data(**kwargs)
        ctx["payment_methods"] = PaymentMethod.objects.order_by(
            "sort_order", "name"
        )
        return ctx

# core/views.py
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import FormView
from django.shortcuts import render
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal

from django.db import models
from django.db.models import Case, DecimalField, F, OuterRef, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce, Greatest

from .forms import GlobalSettingsForm
from .models import GlobalSettings
from tenants.models import Tenant, TenantInterestType
from payments.models import Payment
from invoices.models import Invoice
from invoices.models import SecurityDepositTransaction
from expenses.models import Expense
from properties.models import Property, Unit
from leases.models import Lease, LeaseRenewal
from smart_meter.models import LiveReading
from django.contrib.auth.decorators import login_required

METER_ONLINE_MINUTES = 3


def _annotate_dashboard_lease_financials(queryset):
    money_field = DecimalField(max_digits=12, decimal_places=2)
    zero = Value(Decimal("0.00"), output_field=money_field)
    today = timezone.localdate()

    active_history_monthly_payment = (
        LeaseRenewal.objects.filter(
            lease_id=OuterRef("pk"),
            start_date__lte=today,
            end_date__gte=today,
        )
        .annotate(
            total=(
                Coalesce(F("monthly_rent"), zero)
                + Coalesce(F("society_maintenance"), zero)
                + Coalesce(F("water_charges"), zero)
                + Coalesce(F("internet_charges"), zero)
            )
        )
        .order_by("-renewal_number", "-id")
        .values("total")[:1]
    )

    invoice_total = (
        Invoice.objects.filter(lease_id=OuterRef("pk"))
        .values("lease_id")
        .annotate(total=Coalesce(Sum("amount"), zero))
        .values("total")[:1]
    )

    payment_total = (
        Payment.objects.filter(lease_id=OuterRef("pk"))
        .values("lease_id")
        .annotate(
            total=Coalesce(
                Sum(
                    Case(
                        When(
                            allocation__isnull=False,
                            then=F("allocation__lease_amount"),
                        ),
                        default=F("amount"),
                        output_field=money_field,
                    )
                ),
                zero,
            )
        )
        .values("total")[:1]
    )

    def security_total(tx_type):
        return (
            SecurityDepositTransaction.objects.filter(
                lease_id=OuterRef("pk"),
                type=tx_type,
            )
            .values("lease_id")
            .annotate(total=Coalesce(Sum("amount"), zero))
            .values("total")[:1]
        )

    return (
        queryset.annotate(
            invoice_total=Coalesce(Subquery(invoice_total, output_field=money_field), zero),
            payment_total=Coalesce(Subquery(payment_total, output_field=money_field), zero),
            security_paid_total=Coalesce(
                Subquery(security_total("PAYMENT"), output_field=money_field),
                zero,
            ),
            security_adjust_total=Coalesce(
                Subquery(security_total("ADJUST"), output_field=money_field),
                zero,
            ),
        )
        .annotate(
            list_balance=F("invoice_total") - F("payment_total"),
            list_security_due=Greatest(
                Coalesce(F("security_deposit"), zero)
                - F("security_paid_total")
                - F("security_adjust_total"),
                zero,
                output_field=money_field,
            ),
            list_monthly_payment=Coalesce(
                Subquery(active_history_monthly_payment, output_field=money_field),
                Coalesce(F("monthly_rent"), zero)
                + Coalesce(F("society_maintenance"), zero)
                + Coalesce(F("water_charges"), zero)
                + Coalesce(F("internet_charges"), zero),
                output_field=money_field,
            ),
        )
    )


@login_required
def dashboard(request):
    today = timezone.now().date()
    thirty_days_ago = today - timedelta(days=30)
    lease_ending_cutoff = today + timedelta(days=40)
    recently_ended_cutoff = today - timedelta(days=40)

    total_properties = Property.objects.count()
    total_units = Unit.objects.count()
    active_lease = Lease.objects.filter(
        unit_id=models.OuterRef("pk"),
        start_date__lte=today,
        end_date__gte=today,
    ).exclude(status__in=["ended", "terminated"])
    active_lease_history = LeaseRenewal.objects.filter(
        lease__unit_id=models.OuterRef("pk"),
        start_date__lte=today,
        end_date__gte=today,
    )
    units_with_occupancy = Unit.objects.annotate(
        has_active_lease=models.Exists(active_lease),
        has_active_lease_history=models.Exists(active_lease_history),
    )
    occupied_units = units_with_occupancy.filter(
        models.Q(has_active_lease=True) | models.Q(has_active_lease_history=True)
    ).count()
    vacant_units = (
        units_with_occupancy.select_related("property", "interest_type")
        .filter(has_active_lease=False, has_active_lease_history=False)
        .exclude(status="maintenance")
        .order_by("property__property_name", "unit_number")[:10]
    )
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

    recent_payments = list(
        Payment.objects.select_related(
            "lease",
            "lease__tenant",
            "lease__unit",
            "lease__unit__property",
        )
        .order_by("-payment_date", "-id")[:5]
    )
    recent_lease_ids = [
        payment.lease_id for payment in recent_payments if payment.lease_id
    ]

    invoice_totals = {
        row["lease_id"]: row["total"] or 0
        for row in (
            Invoice.objects.filter(lease_id__in=recent_lease_ids)
            .values("lease_id")
            .annotate(total=models.Sum("amount"))
        )
    }
    payment_totals = {
        row["lease_id"]: row["total"] or 0
        for row in (
            Payment.objects.filter(lease_id__in=recent_lease_ids)
            .values("lease_id")
            .annotate(total=models.Sum("amount"))
        )
    }

    for payment in recent_payments:
        payment.dashboard_balance = (
            invoice_totals.get(payment.lease_id, 0)
            - payment_totals.get(payment.lease_id, 0)
        )

    upcoming_invoices = (
        Invoice.objects.select_related(
            "lease",
            "lease__tenant",
            "lease__unit",
            "lease__unit__property",
        )
        .filter(due_date__gte=today, status__in=["unpaid", "partially_paid"])
        .order_by("due_date", "id")[:5]
    )

    meter_offline_cutoff = timezone.now() - timedelta(minutes=METER_ONLINE_MINUTES)
    offline_meter_readings = (
        LiveReading.objects.select_related(
            "meter",
            "meter__unit",
            "meter__unit__property",
        )
        .filter(
            meter__is_active=True,
            ts__lt=meter_offline_cutoff,
        )
        .order_by("ts", "meter__unit__property__property_name", "meter__unit__unit_number")[:50]
    )

    dashboard_lease_base = Lease.objects.select_related(
        "tenant",
        "unit",
        "unit__property",
    ).only(
        "id",
        "tenant_id",
        "unit_id",
        "start_date",
        "end_date",
        "monthly_rent",
        "society_maintenance",
        "water_charges",
        "internet_charges",
        "security_deposit",
        "status",
        "tenant__id",
        "tenant__first_name",
        "tenant__last_name",
        "tenant__phone",
        "unit__id",
        "unit__property_id",
        "unit__unit_number",
        "unit__property__id",
        "unit__property__property_name",
    )
    dashboard_leases = _annotate_dashboard_lease_financials(dashboard_lease_base)

    ending_soon_leases = (
        dashboard_leases.filter(
            models.Q(status="active", end_date__lte=lease_ending_cutoff)
            | models.Q(status__in=["ended", "inactive"], end_date__gte=recently_ended_cutoff)
        )
        .order_by("end_date", "unit__property__property_name", "unit__unit_number")[:10]
    )

    lease_balances = (
        dashboard_leases.filter(list_balance__gt=0)
        .order_by("-list_balance", "unit__property__property_name", "unit__unit_number")[:10]
    )

    recent_expenses = (
        Expense.objects.select_related("property", "unit", "category")
        .prefetch_related("receipts", "distributions__unit")
        .order_by("-date", "-pk")[:10]
    )

    context = {
        'total_properties': total_properties,
        'TODAY': today,
        'total_units': total_units,
        'occupied_units': occupied_units,
        'vacancy_rate': round(vacancy_rate, 2),
        'vacant_units': vacant_units,
        'total_tenants': total_tenants,
        'total_rent': total_rent,
        'total_payments': total_payments,
        'total_expenses': total_expenses,
        'net_income': total_payments - total_expenses,
        'recent_payments': recent_payments,
        'recent_invoices': Invoice.objects.order_by('-issue_date')[:5],
        'upcoming_invoices': upcoming_invoices,
        'offline_meter_readings': offline_meter_readings,
        'meter_online_minutes': METER_ONLINE_MINUTES,
        'ending_soon_leases': ending_soon_leases,
        'recent_expenses': recent_expenses,
        'lease_balances': lease_balances,
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
from leases.models import LeaseDocumentCategory, LeaseRelationshipType
# core/views.py
from django.views.generic import FormView
from django.urls import reverse_lazy
from django.contrib import messages
from django.core.cache import cache

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
        cache.delete("core.global_settings")
        cache.delete("core.enable_debug_toolbar")
        messages.success(self.request, "Settings saved.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        """
        Add payment_methods so settings.html can render the list.
        """
        ctx = super().get_context_data(**kwargs)
        form = ctx.get("form")
        if form:
            ctx["settings_field_groups"] = [
                (title, icon, [form[name] for name in names if name in form.fields])
                for title, icon, names in form.FIELD_GROUPS
            ]
        ctx["payment_methods"] = PaymentMethod.objects.order_by(
            "sort_order", "name"
        )
        ctx["lease_document_categories"] = LeaseDocumentCategory.objects.order_by(
            "sort_order", "name"
        )
        ctx["tenant_interest_types"] = TenantInterestType.objects.order_by(
            "sort_order", "name"
        )
        ctx["lease_relationship_types"] = LeaseRelationshipType.objects.order_by(
            "sort_order", "name"
        )
        return ctx


def lease_document_category_get(request, pk):
    category = get_object_or_404(LeaseDocumentCategory, pk=pk)
    return JsonResponse({
        "id": category.id,
        "name": category.name,
        "code": category.code,
        "sort_order": category.sort_order,
        "is_active": category.is_active,
    })


@require_POST
def lease_document_category_toggle(request, pk):
    category = get_object_or_404(LeaseDocumentCategory, pk=pk)
    category.is_active = not category.is_active
    category.save(update_fields=["is_active"])
    return JsonResponse({"ok": True})


@require_POST
def lease_document_category_save(request):
    category_id = request.POST.get("id")
    name = (request.POST.get("name") or "").strip()
    code = (request.POST.get("code") or "").strip() or slugify(name)
    try:
        sort_order = int(request.POST.get("sort_order", "50"))
    except ValueError:
        sort_order = 50
    is_active = request.POST.get("is_active") == "1"

    if not name:
        return HttpResponseBadRequest("Name required")
    if not code:
        return HttpResponseBadRequest("Code required")

    if category_id:
        category = get_object_or_404(LeaseDocumentCategory, pk=category_id)
    else:
        category = LeaseDocumentCategory()

    category.name = name
    category.code = code
    category.sort_order = sort_order
    category.is_active = is_active
    category.save()
    return JsonResponse({"ok": True})


def tenant_interest_type_get(request, pk):
    interest_type = get_object_or_404(TenantInterestType, pk=pk)
    return JsonResponse({
        "id": interest_type.id,
        "name": interest_type.name,
        "code": interest_type.code,
        "sort_order": interest_type.sort_order,
        "is_active": interest_type.is_active,
    })


@require_POST
def tenant_interest_type_toggle(request, pk):
    interest_type = get_object_or_404(TenantInterestType, pk=pk)
    interest_type.is_active = not interest_type.is_active
    interest_type.save(update_fields=["is_active"])
    return JsonResponse({"ok": True})


@require_POST
def tenant_interest_type_save(request):
    interest_id = request.POST.get("id")
    name = (request.POST.get("name") or "").strip()
    code = (request.POST.get("code") or "").strip() or slugify(name)
    try:
        sort_order = int(request.POST.get("sort_order", "50"))
    except ValueError:
        sort_order = 50
    is_active = request.POST.get("is_active") == "1"

    if not name:
        return HttpResponseBadRequest("Name required")
    if not code:
        return HttpResponseBadRequest("Code required")

    if interest_id:
        interest_type = get_object_or_404(TenantInterestType, pk=interest_id)
    else:
        interest_type = TenantInterestType()

    interest_type.name = name
    interest_type.code = code
    interest_type.sort_order = sort_order
    interest_type.is_active = is_active
    interest_type.save()
    return JsonResponse({"ok": True})


def lease_relationship_type_get(request, pk):
    relationship_type = get_object_or_404(LeaseRelationshipType, pk=pk)
    return JsonResponse({
        "id": relationship_type.id,
        "name": relationship_type.name,
        "code": relationship_type.code,
        "sort_order": relationship_type.sort_order,
        "is_active": relationship_type.is_active,
    })


@require_POST
def lease_relationship_type_toggle(request, pk):
    relationship_type = get_object_or_404(LeaseRelationshipType, pk=pk)
    relationship_type.is_active = not relationship_type.is_active
    relationship_type.save(update_fields=["is_active"])
    return JsonResponse({"ok": True})


@require_POST
def lease_relationship_type_save(request):
    relationship_id = request.POST.get("id")
    name = (request.POST.get("name") or "").strip()
    code = (request.POST.get("code") or "").strip() or slugify(name)
    try:
        sort_order = int(request.POST.get("sort_order", "50"))
    except ValueError:
        sort_order = 50
    is_active = request.POST.get("is_active") == "1"

    if not name:
        return HttpResponseBadRequest("Name required")
    if not code:
        return HttpResponseBadRequest("Code required")

    if relationship_id:
        relationship_type = get_object_or_404(LeaseRelationshipType, pk=relationship_id)
    else:
        relationship_type = LeaseRelationshipType()

    relationship_type.name = name
    relationship_type.code = code
    relationship_type.sort_order = sort_order
    relationship_type.is_active = is_active
    relationship_type.save()
    return JsonResponse({"ok": True})


from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from .forms import BackupRestoreForm, BackupSettingsForm, BackupUploadForm
from .backup_utils import (
    choices_for,
    create_code_backup,
    create_db_backup,
    create_full_backup,
    create_media_backup,
    delete_backup,
    list_backups,
    load_backup_settings,
    prune_old_backups,
    restore_database,
    restore_full,
    restore_media,
    save_backup_settings,
    save_uploaded_backup,
)


class BackupCenterView(LoginRequiredMixin, UserPassesTestMixin, View):
    template_name = "core/backup_center.html"

    def test_func(self):
        return self.request.user.is_superuser

    def _context(self, settings_form=None):
        config = load_backup_settings()
        backups = list_backups(config)
        selected_db = self.request.GET.get("selected_db")
        selected_media = self.request.GET.get("selected_media")
        selected_full = self.request.GET.get("selected_full")
        return {
            "backup_settings_form": settings_form or BackupSettingsForm(initial=config),
            "db_restore_form": BackupRestoreForm(
                backup_choices=choices_for(backups, "db"),
                initial={"backup_id": selected_db},
            ),
            "media_restore_form": BackupRestoreForm(
                backup_choices=choices_for(backups, "media"),
                initial={"backup_id": selected_media},
            ),
            "full_restore_form": BackupRestoreForm(
                backup_choices=choices_for(backups, "full"),
                initial={"backup_id": selected_full},
            ),
            "backup_upload_form": BackupUploadForm(),
            "backups": backups,
            "fresh_reset_scope": {
                "profile_name": "tms_safe",
                "profile_description": "Fresh reset is disabled until a TMS-specific reset profile is configured.",
                "wipe_total_rows": 0,
                "keep_total_rows": 0,
            },
        }

    def get(self, request):
        return render(request, self.template_name, self._context())

    def post(self, request):
        action = request.POST.get("action")
        config = load_backup_settings()
        try:
            if action == "save_backup_settings":
                form = BackupSettingsForm(request.POST)
                if form.is_valid():
                    save_backup_settings(form.cleaned_data)
                    messages.success(request, "Backup settings saved.")
                    return redirect("core:backup_center")
                return render(request, self.template_name, self._context(settings_form=form))

            if action == "upload_backup":
                form = BackupUploadForm(request.POST, request.FILES)
                if form.is_valid():
                    uploaded = save_uploaded_backup(
                        config,
                        form.cleaned_data["backup_type"],
                        form.cleaned_data["backup_file"],
                    )
                    messages.success(request, f"Backup uploaded: {uploaded.name}")
                else:
                    messages.error(request, "Upload failed. Check the selected type and file extension.")
                return redirect("core:backup_center")

            if action == "backup_db":
                created = create_db_backup(config)
                prune_old_backups(config)
                messages.success(request, f"Database backup created: {created.name}")
            elif action == "backup_media":
                created = create_media_backup(config)
                prune_old_backups(config)
                messages.success(request, f"Media backup created: {created.name}")
            elif action == "backup_code":
                created = create_code_backup(config)
                prune_old_backups(config)
                messages.success(request, f"Code backup created: {created.name}")
            elif action == "backup_full":
                created = create_full_backup(config)
                prune_old_backups(config)
                messages.success(request, f"Full backup created: {created.name}")
            elif action == "restore_db":
                form = BackupRestoreForm(request.POST, backup_choices=choices_for(list_backups(config), "db"))
                if not form.is_valid() or form.cleaned_data["confirm_text"] != "RESTORE DB":
                    messages.error(request, "Type RESTORE DB exactly before restoring the database.")
                else:
                    safety = create_db_backup({**config, "enable_db_backup": True})
                    restore_database(config, form.cleaned_data["backup_id"])
                    messages.success(request, f"Database restore completed. Safety backup created first: {safety.name}")
            elif action == "restore_media":
                form = BackupRestoreForm(request.POST, backup_choices=choices_for(list_backups(config), "media"))
                if not form.is_valid() or form.cleaned_data["confirm_text"] != "RESTORE MEDIA":
                    messages.error(request, "Type RESTORE MEDIA exactly before restoring media.")
                else:
                    safety = create_media_backup({**config, "enable_media_backup": True})
                    restore_media(config, form.cleaned_data["backup_id"])
                    messages.success(request, f"Media restore completed. Safety backup created first: {safety.name}")
            elif action == "restore_full":
                form = BackupRestoreForm(request.POST, backup_choices=choices_for(list_backups(config), "full"))
                if not form.is_valid() or form.cleaned_data["confirm_text"] != "RESTORE FULL":
                    messages.error(request, "Type RESTORE FULL exactly before restoring a full backup.")
                else:
                    safety = create_full_backup({**config, "enable_full_backup": True})
                    restore_full(config, form.cleaned_data["backup_id"])
                    messages.success(request, f"Full restore completed. Safety backup created first: {safety.name}")
            elif action == "fresh_reset":
                messages.error(request, "Fresh reset is intentionally disabled until a TMS reset profile is configured.")
            else:
                messages.error(request, "Unknown backup action.")
        except Exception as exc:
            messages.error(request, f"Backup action failed: {exc}")

        return redirect("core:backup_center")


from django.http import FileResponse


class BackupDownloadView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        return self.request.user.is_superuser

    def get(self, request, backup_id):
        backup = next((item for item in list_backups(load_backup_settings()) if item.id == backup_id), None)
        if not backup:
            raise Http404("Backup not found")
        return FileResponse(open(backup.display_path, "rb"), as_attachment=True, filename=backup.id)


class BackupDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, backup_id):
        try:
            delete_backup(load_backup_settings(), backup_id)
        except Exception as exc:
            return JsonResponse({"success": False, "error": str(exc)}, status=400)
        return JsonResponse({"success": True})


from django.http import Http404, HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from .forms import SuggestionReplyForm, SuggestionTicketForm
from .suggestion_store import (
    STATUS_CHOICES,
    TYPE_CHOICES,
    add_reply,
    create_ticket,
    delete_ticket,
    get_ticket,
    list_tickets,
    update_status,
)


@login_required
def suggestion_list(request):
    selected_status = request.GET.get("status")
    if selected_status is None:
        selected_status = "PENDING"
    selected_type = request.GET.get("type", "")
    tickets = list_tickets(status=selected_status, ticket_type=selected_type)
    return render(request, "core/suggestion_list.html", {
        "tickets": tickets,
        "status_choices": STATUS_CHOICES,
        "type_choices": TYPE_CHOICES,
        "selected_status": selected_status,
        "selected_type": selected_type,
    })


@login_required
def suggestion_create(request):
    if request.method == "POST":
        form = SuggestionTicketForm(request.POST)
        if form.is_valid():
            ticket = create_ticket(
                form.cleaned_data,
                request.user,
                files=request.FILES.getlist("photos"),
            )
            messages.success(request, "Suggestion saved.")
            return redirect("core:suggestion_detail", pk=ticket.id)
    else:
        form = SuggestionTicketForm()
    return render(request, "core/suggestion_form.html", {"form": form})


@login_required
def suggestion_detail(request, pk):
    ticket = get_ticket(pk)
    if not ticket:
        raise Http404("Suggestion not found")

    if request.method == "POST":
        form = SuggestionReplyForm(request.POST)
        selected_status = request.POST.get("status") if request.user.is_staff or request.user.is_superuser else None
        if form.is_valid():
            message = (form.cleaned_data.get("message") or "").strip()
            if message or selected_status:
                add_reply(ticket.id, message, request.user, status=selected_status)
                messages.success(request, "Reply saved.")
                return redirect("core:suggestion_detail", pk=ticket.id)
            messages.error(request, "Reply or status change is required.")
    else:
        form = SuggestionReplyForm()

    ticket = get_ticket(pk)
    return render(request, "core/suggestion_detail.html", {
        "ticket": ticket,
        "form": form,
        "status_choices": STATUS_CHOICES,
    })


@login_required
@require_POST
def suggestion_status_update(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        return HttpResponseForbidden("Not allowed")
    new_status = request.POST.get("status")
    if new_status not in dict(STATUS_CHOICES):
        return JsonResponse({"ok": False, "error": "Invalid status."}, status=400)
    ticket = update_status(pk, new_status)
    if not ticket:
        return JsonResponse({"ok": False, "error": "Suggestion not found."}, status=404)
    return JsonResponse({"ok": True, "status": ticket.status})


@login_required
@require_POST
def suggestion_delete(request, pk):
    ticket = get_ticket(pk)
    if not ticket:
        return JsonResponse(
            {"success": False, "error": "Suggestion not found."},
            status=404,
        )
    can_delete = request.user.is_staff or request.user.is_superuser
    can_delete = can_delete or ticket.user_name_snapshot == request.user.get_username()
    if not can_delete:
        return JsonResponse(
            {"success": False, "error": "Permission denied."},
            status=403,
        )
    if not delete_ticket(pk):
        return JsonResponse(
            {"success": False, "error": "Suggestion not found."},
            status=404,
        )
    return JsonResponse({"success": True})

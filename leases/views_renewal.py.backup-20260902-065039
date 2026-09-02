from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.views.generic import DetailView, FormView, UpdateView
from datetime import timedelta
from .forms_renewal import LeaseHistoryEditForm, LeaseRenewalForm
from .models import Lease
from .models_renewal import LeaseRenewal
from .services.lease_history import (
    copy_previous_history_clauses,
    ensure_original_history,
    is_active_history,
    sync_history_to_master_lease,
)
from .utils.agreement_generator import generate_renewal_agreement_pdf
from .utils.billing import update_billing_on_change


def _copy_clauses_to_renewal(lease, renewal):
    copy_previous_history_clauses(lease, renewal)


class RenewLeaseView(LoginRequiredMixin, FormView):
    template_name = "leases/renewal_form.html"
    form_class = LeaseRenewalForm

    def dispatch(self, request, *args, **kwargs):
        self.lease = get_object_or_404(
            Lease.objects.select_related("tenant", "unit", "unit__property"),
            pk=kwargs["pk"],
        )
        ensure_original_history(
            self.lease,
            user=request.user if request.user.is_authenticated else None,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["lease"] = self.lease
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["lease"] = self.lease
        context["renewals"] = self.lease.renewals.all()
        return context

    def form_valid(self, form):
        lease = self.lease
        with transaction.atomic():
            lease = Lease.objects.select_for_update().get(pk=lease.pk)
            last_number = (
                lease.renewals.aggregate(last=Max("renewal_number"))["last"] or 0
            )
            renewal = form.save(commit=False)
            renewal.lease = lease
            renewal.renewal_number = last_number + 1
            renewal.created_by = (
                self.request.user
                if self.request.user.is_authenticated and self.request.user.pk
                else None
            )
            renewal.updated_by = renewal.created_by
            renewal.is_original = False
            renewal.agreement_date = renewal.agreement_date or renewal.start_date
            renewal.terms = lease.terms
            renewal.save()

            _copy_clauses_to_renewal(lease, renewal)

            old_lease = Lease.objects.get(pk=lease.pk)
            if not lease.original_start_date:
                lease.original_start_date = lease.start_date

            lease.start_date = renewal.start_date
            lease.end_date = renewal.end_date
            lease.lease_months = renewal.lease_months
            lease.agreement_date = renewal.agreement_date
            lease.monthly_rent = renewal.monthly_rent
            lease.society_maintenance = renewal.society_maintenance
            lease.water_charges = renewal.water_charges
            lease.bill_water_charges = renewal.bill_water_charges
            lease.internet_charges = renewal.internet_charges
            lease.agreement_charges = renewal.agreement_charges
            lease.security_deposit = renewal.security_deposit
            lease.rent_increase_percent = renewal.rent_increase_percent
            if lease.status != "active":
                lease.status = "active"
            lease.save(update_fields=[
                "original_start_date",
                "start_date",
                "end_date",
                "lease_months",
                "agreement_date",
                "monthly_rent",
                "society_maintenance",
                "water_charges",
                "bill_water_charges",
                "internet_charges",
                "agreement_charges",
                "security_deposit",                "rent_increase_percent",
                "status",
                "updated_at",
            ])
            update_billing_on_change(
                lease,
                old_lease,
                confirm_security_update=True,
                include_backfill=False,
                update_existing=False,
            )

        messages.success(
            self.request,
            f"Lease renewed. Renewal #{renewal.renewal_number} was created.",
        )
        return redirect("leases:lease_detail", pk=lease.pk)


class LeaseRenewalListView(LoginRequiredMixin, DetailView):
    model = Lease
    template_name = "leases/lease_renewal_list.html"
    context_object_name = "lease"

    def get_queryset(self):
        return Lease.objects.select_related("tenant", "unit", "unit__property").prefetch_related("renewals")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ensure_original_history(
            self.object,
            user=self.request.user if self.request.user.is_authenticated else None,
        )
        first_renewal = self.object.renewals.exclude(is_original=True).order_by("renewal_number").first()
        context["original_period_end"] = (
            first_renewal.start_date - timedelta(days=1)
            if first_renewal else self.object.end_date
        )
        return context


class LeaseHistoryDetailView(LoginRequiredMixin, DetailView):
    model = LeaseRenewal
    template_name = "leases/lease_history_detail.html"
    context_object_name = "history"
    pk_url_kwarg = "renewal_id"

    def get_queryset(self):
        return LeaseRenewal.objects.select_related(
            "lease", "lease__tenant", "lease__unit", "lease__unit__property", "created_by", "updated_by"
        ).prefetch_related("clauses")

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if obj.lease_id != self.kwargs["pk"]:
            raise Http404("Lease history not found.")
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["lease"] = self.object.lease
        context["is_active_history"] = is_active_history(self.object)
        context["inline_form"] = LeaseHistoryEditForm(instance=self.object)
        return context


class LeaseHistoryUpdateView(LoginRequiredMixin, UpdateView):
    model = LeaseRenewal
    form_class = LeaseHistoryEditForm
    template_name = "leases/lease_history_form.html"
    context_object_name = "history"
    pk_url_kwarg = "renewal_id"

    def get_queryset(self):
        return LeaseRenewal.objects.select_related("lease", "lease__tenant", "lease__unit", "lease__unit__property")

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if obj.lease_id != self.kwargs["pk"]:
            raise Http404("Lease history not found.")
        return obj

    def get_success_url(self):
        return reverse("leases:lease_history_detail", kwargs={"pk": self.object.lease_id, "renewal_id": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["lease"] = self.object.lease
        context["is_active_history"] = is_active_history(self.object)
        return context

    def form_valid(self, form):
        is_ajax = self.request.headers.get("x-requested-with") == "XMLHttpRequest"
        active = is_active_history(self.object)
        history = form.save(commit=False)

        history.updated_by = self.request.user if self.request.user.is_authenticated else None
        response = super().form_valid(form)

        if active:
            old_lease = Lease.objects.get(pk=history.lease_id)
            sync_history_to_master_lease(history, user=self.request.user)
            new_lease = Lease.objects.get(pk=history.lease_id)
            update_billing_on_change(
                new_lease,
                old_lease,
                confirm_security_update=True,
                include_backfill=False,
                update_existing=False,
            )
            if not is_ajax:
                messages.success(self.request, "Active lease history updated and master lease recurring charges synced.")
        else:
            if not is_ajax:
                messages.success(self.request, "Old lease history updated without changing the master lease.")

        if is_ajax:
            history.refresh_from_db()
            return JsonResponse({
                "ok": True,
                "message": "Lease history saved.",
                "total_monthly_amount": str(history.total_monthly_amount),
                "updated_at": history.updated_at.strftime("%b %d, %Y %H:%M"),
            })

        return response

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {"ok": False, "message": "Please correct the highlighted value.", "errors": form.errors.get_json_data()},
                status=400,
            )
        return super().form_invalid(form)


@login_required
def generate_renewal_agreement(request, pk, renewal_id):
    lease = get_object_or_404(Lease, pk=pk)
    renewal = get_object_or_404(LeaseRenewal, pk=renewal_id, lease=lease)

    pdf_bytes = generate_renewal_agreement_pdf(lease, renewal, request=request)
    history_label = "original" if renewal.is_original else "renewal"
    filename = f"lease_{lease.pk}_history_{renewal.renewal_number}_{history_label}_agreement.pdf"
    renewal.generated_agreement_pdf.save(filename, ContentFile(pdf_bytes), save=True)
    renewal.updated_by = request.user if request.user.is_authenticated else None
    renewal.save(update_fields=["updated_by", "updated_at"])

    messages.success(request, f"History #{renewal.renewal_number} agreement PDF generated.")
    return redirect("leases:lease_history_detail", pk=lease.pk, renewal_id=renewal.pk)


@login_required
@require_http_methods(["GET", "POST"])
def upload_renewal_signed_copy(request, pk, renewal_id):
    lease = get_object_or_404(Lease, pk=pk)
    renewal = get_object_or_404(LeaseRenewal, pk=renewal_id, lease=lease)

    if request.method == "POST":
        signed_file = request.FILES.get("signed_copy")
        if not signed_file:
            messages.error(request, "Please choose a signed agreement file.")
        else:
            renewal.signed_copy = signed_file
            renewal.is_agreement_signed = True
            renewal.save(update_fields=["signed_copy", "is_agreement_signed", "updated_at"])
            messages.success(request, f"Signed copy uploaded for renewal #{renewal.renewal_number}.")
            return redirect("leases:lease_detail", pk=lease.pk)

    return render(request, "leases/renewal_upload_signed.html", {
        "lease": lease,
        "renewal": renewal,
    })

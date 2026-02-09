# payments/views/allocations.py
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import DetailView, UpdateView
from django.contrib import messages
from django.db import transaction
from django.urls import reverse_lazy

from payments.models import PaymentAllocation
from payments.forms import PaymentAllocationForm,Payment,PaymentForm
from payments.services.allocation import rebuild_allocation


# payments/views/allocations.py
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView

from payments.models import PaymentAllocation
#from leases.utils.billing import security_deposit_totals  # adjust import if different
from core.models import GlobalSettings  # adjust import if different
from leases.models import Lease
from invoices.services import security_deposit_totals
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import DetailView, UpdateView, DeleteView

from payments.models import PaymentAllocation
from payments.forms import PaymentAllocationForm
from payments.services.allocation import rebuild_allocation
from invoices.models import SecurityDepositTransaction

class AllocationDetailView(LoginRequiredMixin, DetailView):
    model = PaymentAllocation
    template_name = "payments/allocation_detail.html"
    context_object_name = "allocation"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        allocation = self.object
        payment = getattr(allocation, "payment", None)
        lease = getattr(payment, "lease", None) if payment else None

        # Provide these for template convenience (your template uses them)
        ctx["payment"] = payment
        ctx["lease"] = lease

        # Security totals (your template expects sec_totals)
        if lease:
            ctx["sec_totals"] = security_deposit_totals(lease)
        else:
            ctx["sec_totals"] = {
                "required": 0,
                "paid_in": 0,
                "refunded": 0,
                "damages": 0,
                "balance_to_collect": 0,
                "currently_held": 0,
            }

        # GlobalSettings (your template uses GLOBAL_SETTINGS.country_code)
        # If you don't have this model, remove these 2 lines and hardcode "+92" in template.
        ctx["GLOBAL_SETTINGS"] = GlobalSettings.objects.first()

        return ctx
from decimal import Decimal
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import UpdateView

from payments.models import Payment, PaymentAllocation
from payments.forms import PaymentForm, PaymentAllocationForm
from payments.services import rebuild_allocation


class AllocationUpdateView(UpdateView):
    """
    Uses payments/payment_form.html to edit BOTH:
      - Payment (amount, date, method, ref, notes, lease)
      - Allocation (lease_amount, security_amount, security_type, mode)
    URL pk can be either:
      - PaymentAllocation.pk  (preferred)
      - Payment.pk           (fallback for legacy rows)
    """
    model = Payment
    form_class = PaymentForm
    template_name = "payments/payment_form.html"

    def _get_allocation_or_payment(self):
        pk = self.kwargs["pk"]

        # 1) Try pk as Allocation ID
        alloc = PaymentAllocation.objects.select_related("payment", "payment__lease").filter(pk=pk).first()
        if alloc:
            return alloc, alloc.payment

        # 2) Fallback: pk is actually Payment ID
        payment = get_object_or_404(Payment.objects.select_related("lease"), pk=pk)

        # Ensure allocation exists (if missing, create via rebuild)
        existing_alloc = getattr(payment, "allocation", None)
        if not existing_alloc:
            rebuild_allocation(
                payment=payment,
                lease_amount=Decimal(payment.amount or 0),
                security_amount=Decimal("0.00"),
                security_type="PAYMENT",
                user=getattr(self.request, "user", None),
                reason="Auto-create allocation (legacy payment edit)",
            )
            payment.refresh_from_db()
            existing_alloc = payment.allocation

        return existing_alloc, payment

    def get_object(self, queryset=None):
        self.allocation, payment = self._get_allocation_or_payment()
        return payment

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["allocation_form"] = PaymentAllocationForm(instance=self.allocation)

        # ---- recent payments selector (same behavior as PaymentCreateView) ----
        size = 10
        try:
            s = int(self.request.GET.get("recent_size", "10"))
            size = s if s in (10, 20, 50) else 10
        except ValueError:
            size = 10

        context["recent_size"] = size
        context["recent_size_options"] = [10, 20, 50]
        context["recent_payments"] = Payment.objects.all().order_by("-id")[:size]

        # ---- tenants/properties (same as create) ----
        include_inactive = self.request.GET.get("include_inactive") == "on"
        if include_inactive:
            context["active_tenants"] = Tenant.objects.all().distinct().order_by("first_name")
            context["tenants"] = Tenant.objects.all().distinct().order_by("first_name")
        else:
            context["active_tenants"] = Tenant.objects.filter(leases__status="active").distinct().order_by("first_name")
            context["tenants"] = Tenant.objects.filter(leases__status="active").distinct().order_by("first_name")

        context["properties"] = Property.objects.all().order_by("property_name")
        context["today"] = timezone.now().date()
        context["nocache"] = timezone.now().timestamp()

        # IMPORTANT:
        # Your payment_form.html uses BOTH `form` (payment form) and `allocation_form`.
        # On allocation edit, `form` is PaymentAllocationForm, so keep `allocation_form` too:
        context["allocation_form"] = context["form"]

        # Provide a "payment-like" object for template blocks that refer to payment fields
        # (optional: only if your template accesses form.instance.payment)
        context["payment"] = getattr(self.object, "payment", None)

        return context



    def get_success_url(self):
        # always go to allocation detail
        return reverse_lazy("payments:allocation_detail", kwargs={"pk": self.allocation.pk})

    @transaction.atomic
    def form_valid(self, form):
        payment = form.save()

        alloc_form = PaymentAllocationForm(self.request.POST, instance=self.allocation)
        if not alloc_form.is_valid():
            return self.form_invalid(form)

        alloc = alloc_form.save(commit=False)

        rebuild_allocation(
            payment=payment,
            lease_amount=alloc.lease_amount,
            security_amount=alloc.security_amount,
            security_type=alloc.security_type,
            user=self.request.user,
            reason="Edited allocation",
        )

        messages.success(self.request, "Payment + Allocation updated successfully.")
        return redirect(self.get_success_url())


from decimal import Decimal, InvalidOperation
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import UpdateView
from django.contrib import messages

from payments.models import PaymentAllocation, Payment
from payments.forms import PaymentForm, PaymentAllocationForm
from payments.services.allocation import rebuild_allocation

from leases.models import Lease
from tenants.models import Tenant
from properties.models import Property, Unit
from django.utils import timezone
from django.db.models import Q

def _dec(v, default="0.00"):
    try:
        return Decimal(str(v or default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)

class AllocationEditView(LoginRequiredMixin, UpdateView):
    """
    Edit using payment_form.html (PaymentForm + PaymentAllocationForm),
    but the URL is keyed by allocation_id.
    """
    model = Payment
    form_class = PaymentForm
    template_name = "payments/payment_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.allocation = get_object_or_404(PaymentAllocation, pk=kwargs["pk"])
        if not self.allocation.payment_id:
            messages.error(request, "This allocation has no linked payment.")
            return redirect("payments:cash_ledger")
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return self.allocation.payment

    def get_success_url(self):
        return reverse_lazy("payments:allocation_detail", kwargs={"pk": self.allocation.pk})

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # your PaymentForm expects lease sometimes; keep it if needed
        if self.object and getattr(self.object, "lease_id", None):
            kwargs["lease"] = self.object.lease
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # allocation form prefilled from allocation
        if self.request.method == "POST":
            ctx["allocation_form"] = PaymentAllocationForm(self.request.POST, instance=self.allocation)
        else:
            ctx["allocation_form"] = PaymentAllocationForm(instance=self.allocation)

        # right-side recent payments (your template expects these)
        size = 10
        try:
            s = int(self.request.GET.get("recent_size", "10"))
            size = s if s in (10, 20, 50) else 10
        except ValueError:
            size = 10

        ctx["recent_size"] = size
        ctx["recent_size_options"] = [10, 20, 50]
        ctx["recent_payments"] = Payment.objects.all().order_by("-id")[:size]

        include_inactive = str(self.request.GET.get("include_inactive", "")).lower() in ("on","1","true","yes")
        lease_qs = Lease.objects.all()
        if not include_inactive:
            lease_qs = lease_qs.filter(status="active")

        ctx["active_tenants"] = Tenant.objects.filter(leases__in=lease_qs).distinct().order_by("first_name")
        ctx["tenants"] = ctx["active_tenants"]
        ctx["properties"] = Property.objects.all().order_by("property_name")
        ctx["today"] = timezone.now().date()
        ctx["nocache"] = timezone.now().timestamp()
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        payment = form.save()
        alloc_form = PaymentAllocationForm(self.request.POST, instance=self.allocation)

        if not alloc_form.is_valid():
            return self.form_invalid(form)

        alloc = alloc_form.save(commit=False)
        lease_amt = _dec(self.request.POST.get("lease_amount"), "0.00")
        sec_amt   = _dec(self.request.POST.get("security_amount"), "0.00")
        total = lease_amt + sec_amt

        if total != payment.amount:
            form.add_error(None, f"Allocation total ({total}) must equal Payment amount ({payment.amount}).")
            return self.form_invalid(form)

        alloc.updated_by = self.request.user
        alloc.save()

        rebuild_allocation(
            payment=payment,
            lease_amount=lease_amt,
            security_amount=sec_amt,
            security_type=alloc.security_type,
            user=self.request.user,
            reason="Edited via AllocationEditView",
        )

        messages.success(self.request, "Allocation updated successfully.")
        return redirect(self.get_success_url())

from django.views.generic import DeleteView
from django.urls import reverse_lazy
from invoices.models import SecurityDepositTransaction
from payments.models import PaymentAllocation

class AllocationDeleteView(LoginRequiredMixin, DeleteView):
    model = PaymentAllocation
    template_name = "payments/allocation_confirm_delete.html"
    context_object_name = "allocation"

    def get_success_url(self):
        return reverse_lazy("payments:cash_ledger")  # or wherever you want to land

    def delete(self, request, *args, **kwargs):
        alloc = self.get_object()
        payment = alloc.payment

        # Delete security rows that were created from this allocation/payment
        SecurityDepositTransaction.objects.filter(allocation=alloc).delete()
        if payment:
            SecurityDepositTransaction.objects.filter(payment=payment).delete()

        # Delete the payment (this will cascade-delete the allocation)
        if payment:
            payment.delete()
        else:
            alloc.delete()

        return redirect(self.get_success_url())

    """
    Delete allocation + its payment + its linked security deposit row.
    IMPORTANT: We delete the PAYMENT, because payment deletion cascades allocation deletion.
    """
    @transaction.atomic
    def post(self, request, pk):
        alloc = get_object_or_404(PaymentAllocation, pk=pk)
        pay = alloc.payment

        # delete security tx linked to this allocation explicitly
        SecurityDepositTransaction.objects.filter(allocation=alloc).delete()

        # delete payment (will cascade delete allocation)
        if pay:
            pay.delete()
            messages.success(request, "Payment (and allocation) deleted successfully.")
        else:
            # if somehow payment missing, delete allocation directly
            alloc.delete()
            messages.success(request, "Allocation deleted successfully.")

        return redirect(reverse_lazy("payments:cash_ledger"))

from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from decimal import Decimal, InvalidOperation

from payments.models import PaymentAllocation
from payments.services.allocation import rebuild_allocation


def _dec(v):
    try:
        return Decimal(v or "0.00")
    except (InvalidOperation, TypeError):
        return Decimal("0.00")


@login_required
@require_POST
def allocation_update_api(request):
    """
    AJAX endpoint used by Cash Ledger split modal
    """
    allocation_id = request.POST.get("allocation_id")
    if not allocation_id:
        return JsonResponse({"error": "Missing allocation_id"}, status=400)

    try:
        alloc = PaymentAllocation.objects.select_related("payment").get(pk=allocation_id)
    except PaymentAllocation.DoesNotExist:
        return JsonResponse({"error": "Allocation not found"}, status=404)

    lease_amt = _dec(request.POST.get("lease_amount"))
    sec_amt   = _dec(request.POST.get("security_amount"))
    sec_type  = (request.POST.get("security_type") or "PAYMENT").upper()

    payment = alloc.payment
    total = lease_amt + sec_amt

    if total != payment.amount:
        return JsonResponse({
            "error": f"Split total ({total}) must equal payment amount ({payment.amount})"
        }, status=400)

    rebuild_allocation(
        payment=payment,
        lease_amount=lease_amt,
        security_amount=sec_amt,
        security_type=sec_type,
        user=request.user,
        reason="Split edited from Cash Ledger",
    )

    return JsonResponse({
        "status": "ok",
        "allocation_id": alloc.id
    })

from django.http import JsonResponse, Http404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from payments.models import PaymentAllocation
from invoices.services import security_deposit_totals

def build_allocation_receipt_message(request, alloc: PaymentAllocation) -> str:
    pay = alloc.payment
    lease = getattr(pay, "lease", None)
    tenant = getattr(lease, "tenant", None)
    unit = getattr(lease, "unit", None)
    prop = getattr(unit, "property", None)

    first_name = getattr(tenant, "first_name", "") or "Customer"
    property_name = getattr(prop, "property_name", "") or ""
    unit_number = getattr(unit, "unit_number", "") or ""
    start_date = getattr(lease, "start_date", None)
    end_date = getattr(lease, "end_date", None)

    totals = security_deposit_totals(lease) if lease else {"required": 0, "balance_to_collect": 0}
    sec_required = totals.get("required") or 0
    sec_balance = totals.get("balance_to_collect") or 0
    sec_status = "Paid" if sec_balance <= 0 else "Pending"

    lines = [
        f"Dear {first_name},",
        f"*Payment received* for {property_name}.",
        f"Unit: {unit_number}",
        (f"Period: {start_date:%b %d, %Y} – {end_date:%b %d, %Y}" if start_date and end_date else ""),
    ]

    if sec_required:
        lines.append(f"Security Deposit: Rs. {float(sec_required):,.2f} ({sec_status})")
        if sec_balance > 0:
            lines.append(f"*Security Deposit Balance: Rs. {float(sec_balance):,.2f}*")

    if getattr(pay, "payment_date", None):
        lines.append(f"Date: {pay.payment_date:%b %d, %Y}")

    lines.extend([
        f"*Total Amount Received: Rs. {float(pay.amount or 0):,.2f}*",
        f"Lease Portion: Rs. {float(alloc.lease_amount or 0):,.2f}",
        f"Security Portion: Rs. {float(alloc.security_amount or 0):,.2f}",
        "",
        "Thank you!",
    ])

    return "\n".join([l for l in lines if l])

@login_required
@require_GET
def api_allocation_receipt_whatsapp(request, pk: int):
    alloc = (PaymentAllocation.objects
             .select_related("payment", "payment__lease", "payment__lease__tenant",
                             "payment__lease__unit", "payment__lease__unit__property")
             .filter(pk=pk).first())
    if not alloc:
        raise Http404("Allocation not found")

    pay = alloc.payment
    phone = getattr(getattr(getattr(pay, "lease", None), "tenant", None), "phone", "") or ""
    message = build_allocation_receipt_message(request, alloc)
    return JsonResponse({"phone": phone, "message": message, "allocation_id": alloc.pk})

from django.http import JsonResponse
from django.views import View
from django.shortcuts import get_object_or_404
from payments.models import PaymentAllocation

class AllocationPrefillApi(View):
    def get(self, request, pk):
        alloc = get_object_or_404(PaymentAllocation.objects.select_related("payment"), pk=pk)
        return JsonResponse({
            "allocation_id": alloc.pk,
            "payment_id": alloc.payment_id,
            "lease_amount": str(alloc.lease_amount or 0),
            "security_amount": str(alloc.security_amount or 0),
            "security_type": (alloc.security_type or "PAYMENT"),
            "total": str((alloc.lease_amount or 0) + (alloc.security_amount or 0)),
        })

from decimal import Decimal, InvalidOperation
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404

from payments.models import PaymentAllocation
from payments.services.allocation import rebuild_allocation
from invoices.services import security_deposit_totals

def _dec(v, default="0.00"):
    try:
        return Decimal(str(v or default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)

@login_required
@require_GET
def allocation_prefill_api(request):
    """
    GET /payments/api/allocations/prefill/?allocation_id=123
    Returns current allocation values for the modal.
    """
    allocation_id = request.GET.get("allocation_id")
    if not allocation_id:
        return HttpResponseBadRequest("allocation_id required")

    alloc = get_object_or_404(PaymentAllocation.objects.select_related("payment", "payment__lease"), pk=allocation_id)

    return JsonResponse({
        "allocation_id": alloc.id,
        "payment_id": alloc.payment_id,
        "payment_amount": str(getattr(alloc.payment, "amount", "0.00") or "0.00"),
        "lease_amount": str(alloc.lease_amount or "0.00"),
        "security_amount": str(alloc.security_amount or "0.00"),
        "security_type": alloc.security_type or "PAYMENT",
    })

@login_required
@require_POST
def allocation_update_api(request):
    """
    POST /payments/api/allocations/update/
    FormData:
      allocation_id, lease_amount, security_amount, security_type
    """
    allocation_id = request.POST.get("allocation_id")
    if not allocation_id:
        return HttpResponseBadRequest("allocation_id required")

    alloc = get_object_or_404(PaymentAllocation.objects.select_related("payment", "payment__lease"), pk=allocation_id)
    payment = alloc.payment
    if not payment:
        return HttpResponseBadRequest("Allocation has no payment.")

    lease_amt = _dec(request.POST.get("lease_amount"), "0.00")
    sec_amt   = _dec(request.POST.get("security_amount"), "0.00")
    sec_type  = (request.POST.get("security_type") or alloc.security_type or "PAYMENT").upper()

    # Must equal payment amount
    total = lease_amt + sec_amt
    if total != (payment.amount or Decimal("0.00")):
        return JsonResponse({"ok": False, "error": f"Split total {total} must equal payment amount {payment.amount}."}, status=400)

    rebuild_allocation(
        payment=payment,
        lease_amount=lease_amt,
        security_amount=sec_amt,
        security_type=sec_type,
        user=request.user,
        reason="Updated via cash ledger modal",
    )

    return JsonResponse({"ok": True})

@login_required
@require_GET
def api_allocation_receipt_whatsapp(request, pk: int):
    """
    Returns JSON {phone, message} that frontend turns into wa.me URL.
    """
    alloc = get_object_or_404(PaymentAllocation.objects.select_related(
        "payment", "payment__lease", "payment__lease__tenant", "payment__lease__unit", "payment__lease__unit__property"
    ), pk=pk)

    pay = alloc.payment
    lease = getattr(pay, "lease", None)
    tenant = getattr(lease, "tenant", None)

    phone = (getattr(tenant, "phone", "") or "").strip()

    sec_totals = security_deposit_totals(lease) if lease else {"balance_to_collect": 0, "required": 0}
    sec_status = "Pending" if (sec_totals.get("balance_to_collect") or 0) > 0 else "Paid"

    # Message includes total payment + both portions
    msg = (
        f"Dear {getattr(tenant,'first_name','')},\n"
        f"*Payment received* for {lease.unit.property.property_name if lease and lease.unit and lease.unit.property else ''}.\n"
        f"Unit: {lease.unit.unit_number if lease and lease.unit else ''}\n"
        f"Period: {lease.start_date:%b %d, %Y} – {lease.end_date:%b %d, %Y}\n"
        f"Security Deposit: Rs. {sec_totals.get('required',0):,.2f} ({sec_status})\n"
        f"Date: {pay.payment_date:%b %d, %Y}\n"
        f"*Total Amount Received: Rs. {(pay.amount or 0):,.2f}*\n"
        f"Lease Portion: Rs. {(alloc.lease_amount or 0):,.2f}\n"
        f"Security Portion: Rs. {(alloc.security_amount or 0):,.2f}\n"
        f"Thank you!"
    )

    return JsonResponse({"phone": phone, "message": msg, "allocation_id": alloc.id})

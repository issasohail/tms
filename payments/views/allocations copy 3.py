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
class AllocationUpdateView(LoginRequiredMixin, UpdateView):
    model = Payment
    form_class = PaymentForm
    template_name = "payments/payment_form.html"

    def get_object(self):
        alloc = get_object_or_404(PaymentAllocation, pk=self.kwargs["pk"])
        self.allocation = alloc
        return alloc.payment

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["allocation_form"] = PaymentAllocationForm(instance=self.allocation)
        return ctx

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

        messages.success(self.request, "Allocation updated successfully.")
        return redirect("payments:allocation_detail", pk=self.allocation.pk)

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


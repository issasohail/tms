# payments/views/allocations.py
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import DetailView, UpdateView
from django.contrib import messages
from django.db import transaction
from django.urls import reverse_lazy

from payments.models import PaymentAllocation
from payments.forms import PaymentAllocationForm
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
    model = PaymentAllocation
    form_class = PaymentAllocationForm
    template_name = "payments/allocation_form.html"

    def get_success_url(self):
        return reverse_lazy("payments:allocation_detail", kwargs={"pk": self.object.pk})

    @transaction.atomic
    def form_valid(self, form):
        alloc = form.save(commit=False)
        alloc.updated_by = self.request.user
        alloc.save()

        rebuild_allocation(
            payment=alloc.payment,
            lease_amount=alloc.lease_amount,
            security_amount=alloc.security_amount,
            security_type=alloc.security_type,
            user=self.request.user,
            reason="Edited allocation",
        )

        messages.success(self.request, "Allocation updated successfully.")
        return redirect(self.get_success_url())


class AllocationDeleteView(LoginRequiredMixin, DeleteView):
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
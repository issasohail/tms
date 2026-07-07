from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import DeleteView, DetailView

from core.models import GlobalSettings
from invoices.models import Invoice, SecurityDepositTransaction
from invoices.services import security_deposit_totals
from payments.models import Payment
from utils.pdf_export import AllocationReceiptPDF


class PaymentDetailView(LoginRequiredMixin, DetailView):
    model = Payment
    template_name = "payments/payment_detail.html"
    context_object_name = "payment"

    def get_queryset(self):
        return super().get_queryset().select_related(
            "detail",
            "lease",
            "lease__tenant",
            "lease__unit",
            "lease__unit__property",
            "payment_method",
        ).prefetch_related(
            Prefetch(
                "lease__security_transactions",
                queryset=SecurityDepositTransaction.objects.select_related(
                    "payment",
                    "payment_detail",
                ),
            ),
            "lease__invoices",
            Prefetch(
                "lease__payments",
                queryset=Payment.objects.select_related("detail"),
            ),
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        payment = self.object
        payment_detail = getattr(payment, "detail", None)
        lease = getattr(payment, "lease", None)

        ctx["payment_detail"] = payment_detail
        ctx["lease"] = lease
        ctx["GLOBAL_SETTINGS"] = GlobalSettings.objects.first()

        if lease:
            ctx["sec_totals"] = security_deposit_totals(lease)
        else:
            ctx["sec_totals"] = {
                "required": Decimal("0.00"),
                "paid_in": Decimal("0.00"),
                "refunded": Decimal("0.00"),
                "damages": Decimal("0.00"),
                "balance_to_collect": Decimal("0.00"),
                "currently_held": Decimal("0.00"),
            }

        return ctx


class PaymentPDFView(LoginRequiredMixin, View):
    def get(self, request, pk):
        payment = get_object_or_404(
            Payment.objects.select_related(
                "detail",
                "lease",
                "lease__tenant",
                "lease__unit",
                "lease__unit__property",
            ),
            pk=pk,
        )
        payment_detail = getattr(payment, "detail", None)
        if not payment_detail:
            messages.error(request, "This payment has no payment detail.")
            return redirect("payments:payment_detail", pk=payment.pk)

        pdf_bytes, filename = AllocationReceiptPDF.generate(payment_detail, request)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class PaymentDeleteView(LoginRequiredMixin, DeleteView):
    model = Payment
    template_name = "payments/payment_confirm_delete.html"
    context_object_name = "payment"

    def get_queryset(self):
        return super().get_queryset().select_related(
            "detail",
            "lease",
            "lease__tenant",
            "lease__unit",
            "lease__unit__property",
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        payment = self.object
        payment_detail = getattr(payment, "detail", None)
        ctx["payment_detail"] = payment_detail
        security_transactions = SecurityDepositTransaction.objects.filter(payment=payment)
        if payment_detail:
            security_transactions = security_transactions | SecurityDepositTransaction.objects.filter(payment_detail=payment_detail)
        ctx["security_transactions"] = security_transactions.distinct()
        return ctx

    def get_success_url(self):
        return reverse_lazy("payments:payment_list")

    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        payment = self.get_object()
        payment_detail = getattr(payment, "detail", None)

        if payment_detail:
            SecurityDepositTransaction.objects.filter(payment_detail=payment_detail).delete()
        SecurityDepositTransaction.objects.filter(payment=payment).delete()
        payment.delete()

        messages.success(request, "Payment deleted. Related payment detail and security deposit movements were reversed.")
        return redirect(self.get_success_url())

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        return self.delete(request, *args, **kwargs)

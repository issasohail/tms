from django.views import View
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from payments.models import PaymentAllocation
from utils.pdf_export import AllocationReceiptPDF


class AllocationPDFView(View):
    def get(self, request, pk):
        allocation = get_object_or_404(
            PaymentAllocation.objects.select_related(
                "payment",
                "payment__lease",
                "payment__lease__tenant",
                "payment__lease__unit",
                "payment__lease__unit__property",
            ),
            pk=pk
        )

        pdf_bytes, filename = AllocationReceiptPDF.generate(allocation, request)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

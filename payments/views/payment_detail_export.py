from django_tables2.views import SingleTableView

from payments.models import PaymentDetail
from payments.tables_payment_detail import PaymentDetailTable
from utils.pdf_export import handle_export


class PaymentDetailExportView(SingleTableView):
    table_class = PaymentDetailTable
    queryset = PaymentDetail.objects.select_related("payment__lease__tenant")

    def get(self, request, *args, **kwargs):
        table = self.get_table()
        export = handle_export(
            request,
            table,
            export_name="payment_details",
            title="Payment Details",
        )
        if export:
            return export
        return super().get(request, *args, **kwargs)

from django_tables2.views import SingleTableView
from payments.tables_allocation import AllocationTable
from payments.models import PaymentAllocation
from utils.pdf_export import handle_export

class AllocationExportView(SingleTableView):
    table_class = AllocationTable
    queryset = PaymentAllocation.objects.select_related(
        "payment__lease__tenant"
    )

    def get(self, request, *args, **kwargs):
        table = self.get_table()
        export = handle_export(
            request,
            table,
            export_name="allocations",
            title="Payment Allocations"
        )
        if export:
            return export
        return super().get(request, *args, **kwargs)

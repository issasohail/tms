from django.urls import path

from payments.views.cash_ledger import CashLedgerView
from payments.views.allocations import AllocationDetailView
from payments.views.allocation_pdf import AllocationPDFView
from payments.views.allocation_api import update_allocation
from payments.views.allocation_export import AllocationExportView
from payments.views import (
    PaymentListView, PaymentDetailView, PaymentCreateView, PaymentUpdateView, PaymentDeleteView,
    get_filtered_leases,
    send_payment_notification,
    get_units_by_property,
    api_payment_receipt_whatsapp,
    invoice_list,
    send_receipt,
    payment_pdf_view,
    send_payment_email,
)
from payments.views.allocations import allocation_update_api

from payments.views.allocations import (
    AllocationDetailView,
    AllocationUpdateView,
    AllocationDeleteView,   # ✅ add
)
from payments.views.cash_ledger import CashLedgerView
from payments.views.allocations import AllocationDetailView
from payments.views.allocation_pdf import AllocationPDFView
from payments.views.allocation_api import update_allocation
from payments.views.allocation_export import AllocationExportView
from payments.views.allocations import api_allocation_receipt_whatsapp
from payments.views.allocation_api import allocation_prefill_api
from django.urls import path
from payments.views.allocations import (
    AllocationDetailView,
    AllocationDeleteView,
    AllocationEditView,
)
from payments.views.allocations import (
    allocation_prefill_api,
    allocation_update_api,
    api_allocation_receipt_whatsapp,
)

app_name = "payments"

urlpatterns = [
    # ---- Payments ----
    path("payments/", PaymentListView.as_view(), name="payment_list"),
    path("payments/create/", PaymentCreateView.as_view(), name="payment_create"),
    path("payments/<int:pk>/", PaymentDetailView.as_view(), name="payment_detail"),
    path("payments/<int:pk>/update/", PaymentUpdateView.as_view(), name="payment_update"),
    path("payments/<int:pk>/delete/", PaymentDeleteView.as_view(), name="payment_delete"),

    # ---- Payment APIs / utilities ----
    path("api/invoices/", invoice_list, name="api_invoice_list"),
    path("get-filtered-leases/", get_filtered_leases, name="get_filtered_leases"),
    path("send-notification/", send_payment_notification, name="send_payment_notification"),
    path("get-units/", get_units_by_property, name="get_units_by_property"),

    # ---- Receipts ----
    path("payment/<int:payment_id>/send-receipt/", send_receipt, name="send_receipt"),
    path("payment/<int:pk>/pdf/", payment_pdf_view, name="payment_pdf"),
    path("payment/<int:pk>/send_email/", send_payment_email, name="send_payment_email"),
    path("api/payment/<int:pk>/whatsapp/", api_payment_receipt_whatsapp, name="api_payment_receipt_whatsapp"),

    # ---- Cash Ledger ----
    path("cash-ledger/", CashLedgerView.as_view(), name="cash_ledger"),

    # ---- Allocations ----
    path("allocation/<int:pk>/", AllocationDetailView.as_view(), name="allocation_detail"),
    path("allocation/<int:pk>/pdf/", AllocationPDFView.as_view(), name="allocation_pdf"),

    # Inline edit (modal) API
    path("allocation/update/", update_allocation, name="allocation_update_api"),
    path(
    "api/allocation/update/",
    allocation_update_api,
    name="allocation_update_api"
),

    # Export to Excel (and/or CSV)
    path("allocations/export/", AllocationExportView.as_view(), name="allocation_export"),
    path("allocations/<int:pk>/edit/", AllocationUpdateView.as_view(), name="allocation_update"),
    path("allocations/<int:pk>/delete/", AllocationDeleteView.as_view(), name="allocation_delete"),
    path("api/allocation/<int:pk>/whatsapp/", api_allocation_receipt_whatsapp, name="api_allocation_receipt_whatsapp"),
    path("api/allocation/<int:pk>/prefill/", allocation_prefill_api, name="allocation_prefill_api"),
    path("api/allocation/<int:pk>/whatsapp/", api_allocation_receipt_whatsapp,
     name="api_allocation_receipt_whatsapp"),
     # allocation pages
    path("allocations/<int:pk>/", AllocationDetailView.as_view(), name="allocation_detail"),
    path("allocations/<int:pk>/edit/", AllocationEditView.as_view(), name="allocation_edit"),
    path("allocations/<int:pk>/delete/", AllocationDeleteView.as_view(), name="allocation_delete"),

    # APIs
    path("api/allocations/prefill/", allocation_prefill_api, name="allocation_prefill_api"),
    path("api/allocations/update/", allocation_update_api, name="allocation_update_api"),
    path("api/allocations/<int:pk>/whatsapp/", api_allocation_receipt_whatsapp, name="api_allocation_receipt_whatsapp"),


]

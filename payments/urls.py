from django.urls import path

from payments.views import (
    PaymentCreateView,
    PaymentUpdateView,
    get_filtered_leases,
    get_units_by_property,
    invoice_list,
    public_payment_receipt,
    send_payment_email,
    send_payment_notification,
    send_receipt,
    api_payment_receipt_whatsapp,
)
from payments.views.payment_detail import (
    PaymentDeleteView,
    PaymentDetailView,
    PaymentPDFView,
)
from payments.views.payment_detail_api import (
    api_payment_detail_receipt_whatsapp,
    payment_detail_prefill_api,
    payment_detail_update_api,
)
from payments.views.payment_list import PaymentListView
from payments.views.payment_detail_export import PaymentDetailExportView


app_name = "payments"


urlpatterns = [
    # ---- Payments ----
    path("", PaymentListView.as_view(), name="payment_list"),
    path("create/", PaymentCreateView.as_view(), name="payment_create"),
    path("<int:pk>/", PaymentDetailView.as_view(), name="payment_detail"),
    path("<int:pk>/update/", PaymentUpdateView.as_view(), name="payment_update"),
    path("<int:pk>/delete/", PaymentDeleteView.as_view(), name="payment_delete"),
    path("<int:pk>/pdf/", PaymentPDFView.as_view(), name="payment_pdf"),

    # ---- Payment APIs / utilities ----
    path("api/invoices/", invoice_list, name="api_invoice_list"),
    path("get-filtered-leases/", get_filtered_leases, name="get_filtered_leases"),
    path("send-notification/", send_payment_notification, name="send_payment_notification"),
    path("get-units/", get_units_by_property, name="get_units_by_property"),
    path("payment/<int:payment_id>/send-receipt/", send_receipt, name="send_receipt"),
    path("payment/<int:pk>/send_email/", send_payment_email, name="send_payment_email"),
    path("api/payment/<int:pk>/whatsapp/", api_payment_receipt_whatsapp, name="api_payment_receipt_whatsapp"),
    path("public/receipt/<path:token>/", public_payment_receipt, name="public_payment_receipt"),

    # ---- Payment detail APIs / exports ----
    path("payment-details/export/", PaymentDetailExportView.as_view(), name="payment_detail_export"),
    path("api/payment-details/prefill/", payment_detail_prefill_api, name="payment_detail_prefill_api"),
    path("api/payment-details/update/", payment_detail_update_api, name="payment_detail_update_api"),
    path("api/payment-details/<int:pk>/whatsapp/", api_payment_detail_receipt_whatsapp, name="api_payment_detail_receipt_whatsapp"),
]

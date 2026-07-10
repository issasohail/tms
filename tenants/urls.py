from django.urls import path

from . import views
from .api import TenantLeasesAPI
from .views import (
    TenantCreateView,
    TenantDeleteView,
    TenantDetailView,
    TenantListView,
    TenantRegistrationSubmissionDetailView,
    TenantRegistrationSubmissionListView,
    TenantUpdateView,
    get_units_by_property,
    ledger_pdf,
    print_tenant_view,
    send_ledger,
    tenant_ajax_update,
)

app_name = "tenants"

urlpatterns = [
    # Tenant URLs
    path("", TenantListView.as_view(), name="tenant_list"),
    path("create/", TenantCreateView.as_view(), name="tenant_create"),
    path(
        "registration/new/",
        views.tenant_public_registration_new,
        name="tenant_public_registration_new",
    ),
    path(
        "registration/<str:token>/",
        views.tenant_public_registration_update,
        name="tenant_public_registration",
    ),
    path(
        "registration-link/new/",
        views.tenant_pre_registration_link_create,
        name="tenant_pre_registration_link_create",
    ),
    path(
        "registration-submissions/",
        TenantRegistrationSubmissionListView.as_view(),
        name="registration_submission_list",
    ),
    path(
        "registration-submissions/<int:pk>/",
        TenantRegistrationSubmissionDetailView.as_view(),
        name="registration_submission_detail",
    ),
    path(
        "registration-submissions/<int:pk>/review/",
        views.tenant_registration_submission_review,
        name="registration_submission_review",
    ),
    path(
        "<int:pk>/lead-inline-update/",
        views.tenant_lead_inline_update,
        name="tenant_lead_inline_update",
    ),
    path("<int:pk>/vehicles/add/", views.tenant_vehicle_add, name="tenant_vehicle_add"),
    path("<int:pk>/family/add/", views.tenant_family_add, name="tenant_family_add"),
    path(
        "<int:pk>/family/create-and-add/",
        views.tenant_family_create_and_add,
        name="tenant_family_create_and_add",
    ),
    path(
        "<int:pk>/inline-update/",
        views.tenant_inline_update,
        name="tenant_inline_update",
    ),
    path(
        "<int:pk>/document-replace/",
        views.tenant_document_replace,
        name="tenant_document_replace",
    ),
    path("<int:pk>/", TenantDetailView.as_view(), name="tenant_detail"),
    path("<int:pk>/update/", TenantUpdateView.as_view(), name="tenant_update"),
    path("<int:pk>/delete/", TenantDeleteView.as_view(), name="tenant_delete"),
    # Ledger URLs
    path(
        "lease/<int:lease_id>/ledger/",
        views.LeaseLedgerView.as_view(),
        name="lease_ledger",
    ),
    path("<int:tenant_id>/ledger/pdf/", ledger_pdf, name="lease_ledger_pdf"),
    path("send-ledger/<int:pk>/", send_ledger, name="send_ledger"),
    # Utility URLs
    path("admin/print_tenant/", print_tenant_view, name="print_tenant"),
    path("get-units/", get_units_by_property, name="get_units_by_property"),
    # API URLs
    path(
        "api/tenants/<int:pk>/leases/",
        TenantLeasesAPI.as_view(),
        name="tenant_leases_api",
    ),
    path("payments/tenant-search/", views.tenant_search, name="tenant_search"),
    path("ajax/update/", tenant_ajax_update, name="tenant_ajax_update"),
    path(
        "ajax/status-toggle/", views.tenant_status_toggle, name="tenant_status_toggle"
    ),
]

# core/urls.py
from django.urls import path
from .views import BackupCenterView, BackupDeleteView, BackupDownloadView, dashboard, SettingsView
from . import views

app_name = "core"

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("settings/", SettingsView.as_view(), name="settings"),
    path(
        "settings/move-out-charges/unit/<int:pk>/",
        views.unit_move_out_charge_update,
        name="unit_move_out_charge_update",
    ),
    path(
        "settings/move-out-charges/building-type/<int:pk>/",
        views.building_type_move_out_charge_update,
        name="building_type_move_out_charge_update",
    ),
    path("building-types/get/<int:pk>/", views.building_type_get, name="building_type_get"),
    path("building-types/toggle/<int:pk>/", views.building_type_toggle, name="building_type_toggle"),
    path("building-types/save/", views.building_type_save, name="building_type_save"),
    path("tenant-reference/<str:kind>/create/", views.tenant_reference_create, name="tenant_reference_create"),
    path("tenant-reference/<str:kind>/<int:pk>/inline/", views.tenant_reference_inline_update, name="tenant_reference_inline_update"),
    path("tenant-reference/<str:kind>/<int:pk>/delete/", views.tenant_reference_delete, name="tenant_reference_delete"),
    path("settings/test-whatsapp-pending-alert/", views.test_whatsapp_pending_request_alert, name="test_whatsapp_pending_request_alert"),
    path("pending-approvals/", views.pending_approvals, name="pending_approvals"),
    path("pending-approvals/<str:kind>/<int:pk>/", views.pending_approval_detail, name="pending_approval_detail"),
    path("pending-approvals/media/<int:pk>/retry-download/", views.retry_pending_media_download, name="retry_pending_media_download"),
    path("pending-approvals/media/<int:pk>/video-frames/", views.save_pending_video_frames, name="save_pending_video_frames"),
    path("pending-approvals/family/<int:pk>/file/<str:field_name>/", views.pending_family_file, name="pending_family_file"),
    path("pending-approvals/<str:kind>/<int:pk>/approve/", views.pending_approval_approve, name="pending_approval_approve"),
    path("pending-approvals/<str:kind>/<int:pk>/reject/", views.pending_approval_reject, name="pending_approval_reject"),
    path("pending-approvals/<str:kind>/<int:pk>/delete/", views.pending_approval_delete, name="pending_approval_delete"),
    path("settings/backup-restore/", BackupCenterView.as_view(), name="backup_center"),
    path("settings/backup-restore/download/<path:backup_id>/", BackupDownloadView.as_view(), name="backup_download"),
    path("settings/backup-restore/delete/<path:backup_id>/", BackupDeleteView.as_view(), name="backup_delete"),
    path("suggestions/", views.suggestion_list, name="suggestion_list"),
    path("suggestions/new/", views.suggestion_create, name="suggestion_create"),
    path("suggestions/<int:pk>/", views.suggestion_detail, name="suggestion_detail"),
    path("suggestions/<int:pk>/status/", views.suggestion_status_update, name="suggestion_status_update"),
    path("suggestions/<int:pk>/delete/", views.suggestion_delete, name="suggestion_delete"),
    path(
        "payment-methods/quick-add/",
        views.payment_method_quick_add,
        name="payment_method_quick_add",
    ),
    path(
        "payment-methods/quick-edit/",
        views.payment_method_quick_edit,
        name="payment_method_quick_edit",
    ),
        # Payment method AJAX APIs (NEW)
    path(
        "payment-methods/get/<int:pk>/",
        views.payment_method_get,
        name="payment_method_get",
    ),
    path(
        "payment-methods/toggle/<int:pk>/",
        views.payment_method_toggle,
        name="payment_method_toggle",
    ),
    path(
        "payment-methods/save/",
        views.payment_method_save,
        name="payment_method_save",
    ),
    path(
        "lease-document-categories/get/<int:pk>/",
        views.lease_document_category_get,
        name="lease_document_category_get",
    ),
    path(
        "lease-document-categories/toggle/<int:pk>/",
        views.lease_document_category_toggle,
        name="lease_document_category_toggle",
    ),
    path(
        "lease-document-categories/save/",
        views.lease_document_category_save,
        name="lease_document_category_save",
    ),
    path(
        "lease-document-categories/<int:pk>/inline/",
        views.lease_document_category_inline_update,
        name="lease_document_category_inline_update",
    ),
    path(
        "tenant-interest-types/get/<int:pk>/",
        views.tenant_interest_type_get,
        name="tenant_interest_type_get",
    ),
    path(
        "tenant-interest-types/toggle/<int:pk>/",
        views.tenant_interest_type_toggle,
        name="tenant_interest_type_toggle",
    ),
    path(
        "tenant-interest-types/save/",
        views.tenant_interest_type_save,
        name="tenant_interest_type_save",
    ),
    path(
        "lease-relationship-types/get/<int:pk>/",
        views.lease_relationship_type_get,
        name="lease_relationship_type_get",
    ),
    path(
        "lease-relationship-types/toggle/<int:pk>/",
        views.lease_relationship_type_toggle,
        name="lease_relationship_type_toggle",
    ),
    path(
        "lease-relationship-types/save/",
        views.lease_relationship_type_save,
        name="lease_relationship_type_save",
    ),
    path(
        "lease-relationship-types/<int:pk>/inline/",
        views.lease_relationship_type_inline_update,
        name="lease_relationship_type_inline_update",
    ),

]

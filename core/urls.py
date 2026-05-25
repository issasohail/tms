# core/urls.py
from django.urls import path
from .views import BackupCenterView, BackupDownloadView, dashboard, SettingsView
from . import views

app_name = "core"

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("settings/", SettingsView.as_view(), name="settings"),
    path("settings/backup-restore/", BackupCenterView.as_view(), name="backup_center"),
    path("settings/backup-restore/download/<path:backup_id>/", BackupDownloadView.as_view(), name="backup_download"),
    path("suggestions/", views.suggestion_list, name="suggestion_list"),
    path("suggestions/new/", views.suggestion_create, name="suggestion_create"),
    path("suggestions/<int:pk>/", views.suggestion_detail, name="suggestion_detail"),
    path("suggestions/<int:pk>/status/", views.suggestion_status_update, name="suggestion_status_update"),
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

]

# core/urls.py
from django.urls import path
from .views import dashboard, SettingsView
from . import views

app_name = "core"

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("settings/", SettingsView.as_view(), name="settings"),
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

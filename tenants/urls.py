from django.urls import path
from . import views
from django.http import JsonResponse
from .models import Tenant
from .views import (
    TenantListView, TenantDetailView, TenantCreateView,
    TenantUpdateView, TenantDeleteView, TenantLedgerView,
    ledger_pdf, send_ledger, print_tenant_view,
    get_units_by_property, BalanceDetailView
)
from .views import TenantListView, tenant_ajax_update
from .api import TenantLeasesAPI
from django.views.decorators.csrf import csrf_exempt

# Import notification views directly from notifications app
from notifications.views import (
    NotificationCreateView,
    notification_list,
    notification_detail
)
from notifications.views import NotificationCreateView

app_name = 'tenants'

urlpatterns = [
    # Tenant URLs
    path('', TenantListView.as_view(), name='tenant_list'),
    path('create/', TenantCreateView.as_view(), name='tenant_create'),
    path('<int:pk>/', TenantDetailView.as_view(), name='tenant_detail'),
    path('<int:pk>/update/', TenantUpdateView.as_view(), name='tenant_update'),
    path('<int:pk>/delete/', TenantDeleteView.as_view(), name='tenant_delete'),




    # Ledger URLs
    path('lease/<int:lease_id>/ledger/',
         views.LeaseLedgerView.as_view(), name='lease_ledger'),
    path('<int:tenant_id>/ledger/pdf/', ledger_pdf, name='lease_ledger_pdf'),
    path('send-ledger/<int:pk>/', send_ledger, name='send_ledger'),

    # Utility URLs
    path('admin/print_tenant/', print_tenant_view, name='print_tenant'),
    path('get-units/', get_units_by_property, name='get_units_by_property'),

    # API URLs
    path('api/tenants/<int:pk>/leases/',
         TenantLeasesAPI.as_view(), name='tenant_leases_api'),

    # Notification URLs (using direct imports)
    path('create/', NotificationCreateView.as_view(), name='create'),
    path('notifications/', notification_list, name='notification_list'),
    path('notifications/<int:pk>/', notification_detail,
         name='notification_detail'),
    path('payments/tenant-search/', views.tenant_search, name='tenant_search'),

    path('ajax/update/', tenant_ajax_update, name='tenant_ajax_update'),


]

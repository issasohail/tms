from .views import invoice_item_inline_update
from . import views
from . import services
from .views import (
    InvoiceListView,
    InvoiceCreateView,
    InvoiceUpdateView,
    InvoiceDetailView,
    InvoiceDeleteView,
    send_invoice_email,
    generate_monthly_invoices,
    InvoicePDFView,
    search_invoices_by_item_description,
    CategoryListView, CategoryCreateView, CategoryUpdateView, CategoryDeleteView,
    category_inline_update,    RecurringChargeListView, RecurringChargeCreateView, RecurringChargeUpdateView, RecurringChargeDeleteView,
    WaterBillListView, WaterBillCreateView,  waterbill_post, WaterBillUpdateView,
    run_billing_now, RecurringWizardView,
    api_leases_filtered,
    api_recurring_for_lease,    api_recurring_for_lease,
    api_recurring_update, invoices_bulk_delete_confirm, invoices_bulk_delete_preview,
    api_recurring_delete, invoices_bulk_delete,
    api_recurring_list, api_recurring_create,   api_recurring_backfill,

)
from .views import api_billing_preview_current, api_billing_generate_current
from .views import api_units_for_property, api_tenants_for_property
from .views import run_billing_current
from django.urls import path
from . import views

app_name = 'invoices'

urlpatterns = [
    # List/Create
    path('', InvoiceListView.as_view(), name='invoice_list'),
    path('create/', InvoiceCreateView.as_view(), name='invoice_create'),

    # Detail/Update/Delete
    path('<int:pk>/', InvoiceDetailView.as_view(), name='invoice_detail'),
    path('<int:pk>/update/', InvoiceUpdateView.as_view(), name='invoice_update'),
    path('<int:pk>/delete/', InvoiceDeleteView.as_view(), name='invoice_delete'),

    # Actions
    path('<int:pk>/send-email/', send_invoice_email, name='send_email'),
    path('<int:pk>/pdf/', InvoicePDFView.as_view(), name='invoice_pdf'),

    # Bulk Operations
    path('generate-monthly/', generate_monthly_invoices, name='generate_monthly'),

    # Search
    path('search/', search_invoices_by_item_description, name='search'),
    path('api/categories/create/', views.category_create_ajax,
         name='category_create_ajax'),

    path('api/tenants-for-unit/', views.api_tenants_for_unit,
         name='api_tenants_for_unit'),
    path('categories/', CategoryListView.as_view(), name='category_list'),
    path('categories/add/', CategoryCreateView.as_view(), name='category_add'),
    path('categories/<int:pk>/edit/',
         CategoryUpdateView.as_view(), name='category_edit'),
    path('categories/<int:pk>/delete/',
         CategoryDeleteView.as_view(), name='category_delete'),
    path('categories/<int:pk>/inline/', category_inline_update,
         name='category_inline_update'),
    path("<int:pk>/items/bulk-update/", views.invoice_items_bulk_update,
         name="invoice_items_bulk_update"),
    path("export.csv", views.export_invoices_csv, name="invoice_export_csv"),
    path('items/<int:pk>/inline/', invoice_item_inline_update,
         name='item_inline_update'),
    path('recurring/', RecurringChargeListView.as_view(), name='recurring_list'),
    path('recurring/add/',  RecurringChargeCreateView.as_view(),
         name='recurring_add'),
    path('recurring/<int:pk>/edit/',
         RecurringChargeUpdateView.as_view(), name='recurring_edit'),
    path('recurring/<int:pk>/delete/',
         RecurringChargeDeleteView.as_view(), name='recurring_delete'),

    # Water bills
    path('water-bills/', WaterBillListView.as_view(), name='waterbill_list'),
    path('water-bills/add/', WaterBillCreateView.as_view(), name='waterbill_add'),
    path('water-bills/<int:pk>/edit/',
         WaterBillUpdateView.as_view(), name='waterbill_edit'),
    path('water-bills/<int:pk>/post/', waterbill_post, name='waterbill_post'),


    # One-click monthly run
    path('run-billing/', run_billing_now, name='run_billing'),

    path('recurring/wizard/', RecurringWizardView.as_view(),
         name='recurring_wizard'),
    path('api/leases-filtered/', api_leases_filtered, name='api_leases_filtered'),
    path('api/recurring-for-lease/<int:lease_id>/',
         api_recurring_for_lease, name='api_recurring_for_lease'),
    path('api/recurring/<int:pk>/update/',
         api_recurring_update, name='api_recurring_update'),
    path('api/recurring/<int:pk>/delete/',
         api_recurring_delete, name='api_recurring_delete'),




    # Right-side grid data
    path('api/recurring-list/', api_recurring_list, name='api_recurring_list'),

    # Inline CRUD
    path('api/recurring/create/', api_recurring_create,
         name='api_recurring_create'),


    # Backfill after new with past start_date
    path('api/recurring/<int:pk>/backfill/',
         api_recurring_backfill, name='api_recurring_backfill'),
    path('api/units-for-property/', api_units_for_property,
         name='api_units_for_property'),
    path('api/tenants-for-property/', api_tenants_for_property,
         name='api_tenants_for_property'),
    path('run-billing/current/', run_billing_current, name='run_billing_current'),

    # at top (or extend existing import)


    # inside urlpatterns (group with other API routes)
    path('api/billing/preview-current/', api_billing_preview_current,
         name='api_billing_preview_current'),
    path('api/billing/generate-current/', api_billing_generate_current,
         name='api_billing_generate_current'),
    path('api/billing/preview-for', views.api_billing_preview_for,
         name='api_billing_preview_for'),
    path('api/billing/generate-for', views.api_billing_generate_for,
         name='api_billing_generate_for'),
    path('api/tenants/', views.api_tenants, name='api_tenants'),
    path('api/properties/', views.api_properties, name='api_properties'),
    path('api/units/', views.api_units_for_property, name='api_units'),

    path('api/leases/', views.api_leases, name='api_leases'),

    # invoices/urls.py
    path("api/bulk-delete/for/", views.api_invoices_bulk_delete_for,
         name="invoices_bulk_delete_for"),
    # invoices/urls.py
    path("invoices/bulk-delete/", views.invoices_bulk_delete,
         name="invoices_bulk_delete"),
    # invoices/urls.py
    path("invoices/bulk-delete/preview/", views.invoices_bulk_delete_preview,
         name="invoices_bulk_delete_preview"),
    path("invoices/bulk-delete/confirm/", views.invoices_bulk_delete_confirm,
         name="invoices_bulk_delete_confirm"),
    # path("api/invoice/<int:pk>/whatsapp/",
    #    views.invoice_whatsapp_message, name="invoice_whatsapp_message"),
    path("api/invoice/<int:pk>/whatsapp/",
         views.api_invoice_whatsapp, name="api_invoice_whatsapp"),
     
     path("api/security/<int:pk>/whatsapp/", views.api_security_receipt_whatsapp, name="api_security_receipt_whatsapp"),

]

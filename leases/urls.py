from django.urls import path
from .import views
from properties import views as property_views
from . import views_lease_photos

from .views import (
    LeaseListView, LeaseDetailView, LeaseCreateView,
    LeaseUpdateView, LeaseDeleteView, GetUnitsView,
    RenewLeaseView, PrintLeaseView, generate_lease_pdf,
    lease_balance, lease_print, lease_email, UnitAutocomplete, get_units_by_property, get_tenants,
    get_lease_info, DashboardView, CustomRenewForm, CustomRenewView, LeaseLedgerView, lease_ledger_pdf,
    send_ledger_email, export_ledger_excel, GenerateLeaseAgreementView, LeaseTemplateCreateView, LeaseTemplateDeleteView, LeaseTemplateListView, LeaseTemplateUpdateView,
    SendAgreementEmailView, set_default_template, generate_agreement_pdf,
    UploadSignedCopyView, send_agreement_email,
)
from properties import views as property_views
from django.urls import path
from leases import views
from .views import edit_clauses
from .views_pcr import pcr_gallery, pcr_photo_upload, pcr_photo_delete
from .views_pcr_export import export_photos_to_pdf_and_attach
from django.urls import path
from . import views_lease_photos as lpv  # <- import the file that has the view

from .views_lease_photos import photos_page, photos_grid, photo_add, photo_delete, photo_update, photos_export_pdf, photo_viewer
from .views import (
    SecurityDepositListView,
    SecurityDepositCreateView,
    SecurityDepositUpdateView,
    SecurityDepositDeleteView,
)
app_name = 'leases'

urlpatterns = [
    path('', LeaseListView.as_view(), name='lease_list'),
    path('new/', LeaseCreateView.as_view(), name='lease_create'),

    # Lease CRUD operations
    path('<int:pk>/', LeaseDetailView.as_view(), name='lease_detail'),
    path('<int:pk>/edit/', LeaseUpdateView.as_view(), name='lease_update'),
    path('<int:pk>/delete/', LeaseDeleteView.as_view(), name='lease_delete'),
    path('<int:pk>/renew/', RenewLeaseView.as_view(), name='lease_renew'),


    # Printing and PDF
    path('<int:pk>/print/', PrintLeaseView.as_view(), name='lease_print'),
    path('<int:pk>/pdf/', generate_lease_pdf, name='lease-pdf'),

    # Email
    path('<int:pk>/email/', lease_email, name='lease_email'),

    # AJAX/API endpoints
    path('get_units/', GetUnitsView.as_view(), name='get_units'),
    path('unit-autocomplete/', UnitAutocomplete.as_view(),
         name='unit-autocomplete'),
    path('api/lease-balance/<int:pk>/', lease_balance, name='api_lease_balance'),



    path('tenants/get/', views.get_tenants, name='get_tenants'),

    path('leases/get-info/', views.get_lease_info, name='get_lease_info'),

    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('<int:pk>/custom-renew/', CustomRenewView.as_view(), name='custom_renew'),

    path('ledger/', LeaseLedgerView.as_view(), name='lease_ledger'),
    # Ledger for a specific lease (pk)
    path('<int:pk>/ledger/', LeaseLedgerView.as_view(), name='lease_ledger_by_pk'),

    # PDF/Excel/Email routes that REQUIRE a lease id
    path('<int:lease_id>/ledger/pdf/',
         lease_ledger_pdf,   name='lease_ledger_pdf'),
    path('<int:lease_id>/ledger/xlsx/',
         export_ledger_excel, name='export_ledger_excel'),
    path('<int:lease_id>/ledger/email/',
         send_ledger_email, name='send_ledger_email'),
     path('lease/<int:pk>/generate-docx/', views.generate_agreement_docx, name='generate_agreement_docx'),

    path("ajax/units/", views.get_units_by_property, name="get_units_by_property"),
    path('get_units/', views.get_units, name='get_units'),

    path('<int:pk>/generate/', GenerateLeaseAgreementView.as_view(),
         name='generate_agreement'),
    path('<int:pk>/email/', SendAgreementEmailView.as_view(),
         name='send_agreement_email'),
    path('<int:pk>/upload-signed-copy/',
         UploadSignedCopyView.as_view(), name='upload_signed_copy'),
    path('templates/', LeaseTemplateListView.as_view(), name='template_list'),
    path('templates/new/', LeaseTemplateCreateView.as_view(), name='template_create'),
    path('templates/<int:pk>/edit/',
         LeaseTemplateUpdateView.as_view(), name='template_update'),
    path('templates/<int:pk>/delete/',
         LeaseTemplateDeleteView.as_view(), name='template_delete'),
    path('templates/<int:pk>/set-default/',
         set_default_template, name='set_default_template'),

    # leases/urls.py



    path('lease/<int:lease_id>/agreement/',
         views.generate_lease_agreement, name='generate_lease_agreement'),
    # leases/urls.py
    path('lease/<int:pk>/edit-clauses/',
         views.edit_clauses, name='edit_clauses'),

    path('lease/<int:pk>/generate-pdf/',
         generate_agreement_pdf, name='generate_agreement_pdf'),

    path('lease/<int:pk>/email-agreement/',
         send_agreement_email, name='send_agreement_email'),
    path('<int:lease_id>/download-preview/',
         views.download_preview_pdf, name='download_preview'),
    path("ajax/tenant-quick-search/",
         views.tenant_quick_search, name="tenant_quick_search"),
    path("leases/<int:pk>/family/add/",
         views.lease_family_add, name="lease_family_add"),

    path("lease/<int:lease_id>/pcr/gallery/", pcr_gallery, name="pcr_gallery"),
    path("lease/<int:lease_id>/pcr/upload/",
         pcr_photo_upload, name="pcr_photo_upload"),
    path("pcr/photo/<int:photo_id>/delete/",
         pcr_photo_delete, name="pcr_photo_delete"),
    path("lease/<int:lease_id>/pcr/export-photos-pdf/", export_photos_to_pdf_and_attach,
         name="pcr_export_photos_pdf"),

    path("lease/<int:lease_id>/photos/grid/", photos_grid, name="photos_grid"),


    path("lease/<int:lease_id>/photos/", photos_page, name="photos_page"),
    path("lease/<int:lease_id>/photos/grid/", photos_grid, name="photos_grid"),
    path("lease/<int:lease_id>/photos/add/", photo_add, name="photo_add"),
    path("photo/<int:photo_id>/delete/", photo_delete, name="photo_delete"),
    path("photo/<int:photo_id>/update/", photo_update, name="photo_update"),
    path("lease/<int:lease_id>/photos/export/",
         photos_export_pdf, name="photos_export"),
    path("photo/<int:photo_id>/view/", photo_viewer, name="photo_viewer"),
    path("lease/<int:lease_id>/photos/", lpv.photos_page, name="photos_page"),
    path("lease/<int:lease_id>/photos/grid/",
         lpv.photos_grid, name="photos_grid"),
    path("lease/<int:lease_id>/photos/export/",
         lpv.photos_export_pdf, name="photos_export_pdf"),
    path(
        "lease/<int:lease_id>/photos/export/stream/",
        lpv.photos_export_pdf_stream,
        name="photos_export_pdf_stream",
    ),
    path("photo/<int:photo_id>/view/", lpv.photo_viewer, name="photo_viewer"),
    path("photo/<int:photo_id>/download/",
         lpv.photo_download, name="photo_download"),
    path("lease/<int:lease_id>/deleted-photos/",
         views_lease_photos.deleted_photos_view, name="deleted_photos_view"),
    path("lease/<int:lease_id>/deleted-photos/delete/",
         views_lease_photos.deleted_photos_delete, name="deleted_photos_delete"),
    path("lease/<int:lease_id>/deleted-photos/delete-all/",
         views_lease_photos.deleted_photos_delete_all, name="deleted_photos_delete_all"),
         path(
        'leases/<int:lease_pk>/security/',
        SecurityDepositListView.as_view(),
        name='lease_security_list'
    ),
    path(
        'leases/<int:lease_pk>/security/add/',
        SecurityDepositCreateView.as_view(),
        name='lease_security_add'
    ),
    path(
        'leases/<int:lease_pk>/security/<int:pk>/edit/',
        SecurityDepositUpdateView.as_view(),
        name='lease_security_edit'
    ),
    path(
        'leases/<int:lease_pk>/security/<int:pk>/delete/',
        SecurityDepositDeleteView.as_view(),
        name='lease_security_delete'
    ),
    path(
        "default-clauses/",
        views.default_clause_list,
        name="default_clause_list",
    ),
    path(
        "default-clauses/add/",
        views.default_clause_create,
        name="default_clause_create",
    ),
    path(
        "default-clauses/<int:pk>/edit/",
        views.default_clause_edit,
        name="default_clause_edit",
    ),

    # Per-lease clause editor
    path(
        "leases/<int:lease_id>/clauses/",
        views.lease_clause_edit,
        name="lease_clause_edit",
    ),
     path('lease/<int:pk>/generate-pdf/', views.generate_agreement_pdf, name='generate_agreement_pdf'),
     path('<int:lease_id>/download-preview-docx/', views.download_preview_docx, name='download_preview_docx'),
     path("lease/<int:pk>/clauses/reset/", views.reset_clauses_from_default, name="reset_clauses_from_default"),


     



]

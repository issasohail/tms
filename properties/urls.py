from django.urls import path
from . import views
from .views import (
    PropertyListView, PropertyDetailView, PropertyCreateView,
    PropertyUpdateView, PropertyDeleteView,
    UnitListView, UnitDetailView, UnitCreateView,
    UnitUpdateView, UnitDeleteView,
)
app_name = 'properties'

urlpatterns = [
    path('', PropertyListView.as_view(), name='property_list'),
    path('create/', views.PropertyCreateView.as_view(), name='property_create'),
    path('<int:pk>/', views.PropertyDetailView.as_view(), name='property_detail'),
    path('<int:pk>/update/', views.PropertyUpdateView.as_view(),
         name='property_update'),
    path('<int:pk>/delete/', views.PropertyDeleteView.as_view(),
         name='property_delete'),





    # Unit URLs
    path('units/', UnitListView.as_view(), name='unit_list'),
    path('units/<int:pk>/', UnitDetailView.as_view(), name='unit_detail'),
    path('units/<int:pk>/media/', views.unit_media_page, name='unit_media'),
    path('units/<int:pk>/media/share-link/',
         views.unit_media_share_link, name='unit_media_share_link'),
    path('units/<int:pk>/media/sort/',
         views.unit_media_sort, name='unit_media_sort'),
    path('units/<int:pk>/media/export/pdf/',
         views.unit_media_export_pdf, name='unit_media_export_pdf'),
    path('units/<int:pk>/media/export/docx/',
         views.unit_media_export_docx, name='unit_media_export_docx'),
    path('units/<int:pk>/whatsapp/vacancy/',
         views.unit_vacancy_whatsapp, name='unit_vacancy_whatsapp'),
    path('units/<int:pk>/vacant-notice-leads/',
         views.unit_vacant_notice_leads, name='unit_vacant_notice_leads'),
    path('units/vacant-summary-message/',
         views.unit_vacant_summary_message, name='unit_vacant_summary_message'),
    path('units/<int:pk>/media/<int:media_id>/update/',
         views.unit_media_update, name='unit_media_update'),
    path('units/<int:pk>/media/<int:media_id>/delete/',
         views.unit_media_delete, name='unit_media_delete'),
    path('units/create/', UnitCreateView.as_view(), name='unit_create'),
    path('units/<int:pk>/edit/', UnitUpdateView.as_view(), name='unit_update'),
    path('units/<int:pk>/delete/', UnitDeleteView.as_view(), name='unit_delete'),

    path('<int:pk>/media/', views.property_media_page, name='property_media'),
    path('<int:pk>/media/share-link/',
         views.property_media_share_link, name='property_media_share_link'),
    path('<int:pk>/media/sort/',
         views.property_media_sort, name='property_media_sort'),
    path('<int:pk>/media/export/pdf/',
         views.property_media_export_pdf, name='property_media_export_pdf'),
    path('<int:pk>/media/export/docx/',
         views.property_media_export_docx, name='property_media_export_docx'),
    path('<int:pk>/media/<int:media_id>/update/',
         views.property_media_update, name='property_media_update'),
    path('<int:pk>/media/<int:media_id>/delete/',
         views.property_media_delete, name='property_media_delete'),

    path('units/inline-update/', views.unit_inline_update,
         name='unit_inline_update'),

    path('public/unit-media/<path:token>/<int:media_id>/',
         views.unit_media_public_file, name='unit_media_public_file'),
    path('public/unit-media/<path:token>/',
         views.unit_media_public_share, name='unit_media_public_share'),
    path('public/media/<path:token>/<int:media_id>/',
         views.unit_media_public_file, name='media_public_file'),
    path('public/media/<path:token>/',
         views.unit_media_public_share, name='media_public_share'),
]

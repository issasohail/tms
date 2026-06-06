from django.urls import path

from . import views

app_name = "maintenance"

urlpatterns = [
    path("", views.MaintenanceRequestListView.as_view(), name="request_list"),
    path("add/", views.MaintenanceRequestCreateView.as_view(), name="request_add"),
    path("quick-add/", views.request_quick_add, name="request_quick_add"),
    path("quick-add/related/", views.request_quick_add_related, name="request_quick_add_related"),
    path("categories/manage/", views.category_manage, name="category_list"),
    path("categories/", views.category_list_json, name="category_list_json"),
    path("categories/add/", views.category_create, name="category_create"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
    path("share/<str:token>/", views.public_media_share, name="public_media_share"),
    path("share/<str:token>/file/<int:media_id>/", views.public_media_file, name="public_media_file"),
    path("<int:pk>/", views.MaintenanceRequestDetailView.as_view(), name="request_detail"),
    path("<int:pk>/edit/", views.MaintenanceRequestUpdateView.as_view(), name="request_edit"),
    path("<int:pk>/delete/", views.MaintenanceRequestDeleteView.as_view(), name="request_delete"),
    path("<int:pk>/inline-update/", views.request_inline_update, name="request_inline_update"),
    path("<int:pk>/upload/", views.request_media_upload, name="request_media_upload"),
    path("<int:pk>/whatsapp/", views.request_whatsapp, name="request_whatsapp"),
    path("media/<int:pk>/edit/", views.MaintenanceMediaUpdateView.as_view(), name="media_edit"),
    path("media/<int:pk>/description/", views.media_description_update, name="media_description_update"),
    path("media/<int:pk>/delete/", views.maintenance_media_delete, name="media_delete"),
]

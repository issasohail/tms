from django.urls import path

from . import views

app_name = "maintenance"

urlpatterns = [
    path("", views.MaintenanceRequestListView.as_view(), name="request_list"),
    path("add/", views.MaintenanceRequestCreateView.as_view(), name="request_add"),
    path("<int:pk>/", views.MaintenanceRequestDetailView.as_view(), name="request_detail"),
    path("<int:pk>/edit/", views.MaintenanceRequestUpdateView.as_view(), name="request_edit"),
    path("<int:pk>/delete/", views.MaintenanceRequestDeleteView.as_view(), name="request_delete"),
    path("media/<int:pk>/edit/", views.MaintenanceMediaUpdateView.as_view(), name="media_edit"),
    path("media/<int:pk>/delete/", views.maintenance_media_delete, name="media_delete"),
]

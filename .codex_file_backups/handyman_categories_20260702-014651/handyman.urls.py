from django.urls import path

from . import views

app_name = "handyman"

urlpatterns = [
    path("", views.HandymanListView.as_view(), name="handyman_list"),
    path("add/", views.HandymanCreateView.as_view(), name="handyman_add"),
    path("<int:pk>/", views.HandymanDetailView.as_view(), name="handyman_detail"),
    path("<int:pk>/edit/", views.HandymanUpdateView.as_view(), name="handyman_edit"),
    path("categories/", views.category_settings, name="category_settings"),
    path("maintenance/<int:request_id>/assign/", views.assign_to_maintenance, name="assign_to_maintenance"),
]

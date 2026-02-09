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
    path('units/create/', UnitCreateView.as_view(), name='unit_create'),
    path('units/<int:pk>/edit/', UnitUpdateView.as_view(), name='unit_update'),
    path('units/<int:pk>/delete/', UnitDeleteView.as_view(), name='unit_delete'),

    path('units/inline-update/', views.unit_inline_update,
         name='unit_inline_update')
]

from django.urls import path
from . import views

app_name = 'utilities'

urlpatterns = [
    path('', views.UtilityListView.as_view(), name='utility_list'),
    path('create/', views.UtilityCreateView.as_view(), name='utility_create'),
    path('<int:pk>/', views.UtilityDetailView.as_view(), name='utility_detail'),
    path('<int:pk>/update/', views.UtilityUpdateView.as_view(),
         name='utility_update'),
    path('<int:pk>/delete/', views.UtilityDeleteView.as_view(),
         name='utility_delete'),
    path('<int:pk>/distribute/', views.distribute_utility,
         name='utility_distribute'),
]

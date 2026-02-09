from django.urls import path
from notifications.views import (
    NotificationCreateView,
    notification_list,
    notification_detail,
    CreateMessageView,
    NotificationListView,
    NotificationDeleteView,
    mark_all_as_read,
    NotificationUpdateView
)

app_name = 'notifications'

urlpatterns = [
    path('', NotificationListView.as_view(), name='list'),
    path('create/', NotificationCreateView.as_view(), name='create'),
    path('<int:pk>/', notification_detail, name='detail'),
    path('<int:pk>/delete/', NotificationDeleteView.as_view(), name='delete'),
    path('<int:pk>/edit/', NotificationUpdateView.as_view(), name='edit'),
    path('mark-all-read/', mark_all_as_read, name='mark_all_read'),
    path('message/create/', CreateMessageView.as_view(), name='create_message'),
]

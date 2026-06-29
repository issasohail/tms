from django.urls import path

from . import views

app_name = "whatsapp"

urlpatterns = [
    path("webhook/", views.webhook, name="webhook"),
    path("webhook/logs/", views.webhook_log_list, name="webhook_log_list"),
    path("send/<str:object_type>/<int:object_id>/<str:action>/", views.send_object_message, name="send_object_message"),
    path("test/hello-world/", views.send_hello_world_test, name="send_hello_world_test"),
]

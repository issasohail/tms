from django.urls import path

from . import views

app_name = "whatsapp"

urlpatterns = [
    path("webhook/", views.webhook, name="webhook"),
    path("webhook/logs/", views.webhook_log_list, name="webhook_log_list"),
    path("webhook/messages/<int:message_log_id>/replay-ai/", views.replay_ai_message, name="replay_ai_message"),
    path("utility-templates/", views.utility_template_list, name="utility_template_list"),
    path("utility-templates/<int:pk>/", views.utility_template_edit, name="utility_template_edit"),
    path("send/<str:object_type>/<int:object_id>/<str:action>/", views.send_object_message, name="send_object_message"),
    path("test/hello-world/", views.send_hello_world_test, name="send_hello_world_test"),
]

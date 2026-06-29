from django.urls import path

from . import views

app_name = "whatsapp"

urlpatterns = [
    path("webhook/", views.webhook, name="webhook"),
    path("test/hello-world/", views.send_hello_world_test, name="send_hello_world_test"),
]

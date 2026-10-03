from django.urls import path

from punjab_estamp import views


app_name = "punjab_estamp"

urlpatterns = [
    path(
        "workflow/<int:lease_id>/<int:history_id>/prepare/",
        views.prepare,
        name="prepare",
    ),
    path(
        "workflow/<int:lease_id>/<int:history_id>/replacement/",
        views.replacement,
        name="replacement",
    ),
    path(
        "workflow/<int:lease_id>/<int:history_id>/state/",
        views.state,
        name="state",
    ),
    path(
        "workflow/<int:lease_id>/<int:history_id>/configuration/",
        views.configuration_update,
        name="configuration_update",
    ),
    path(
        "workflow/<int:lease_id>/<int:history_id>/launch/",
        views.launch,
        name="launch",
    ),
    path(
        "workflow/<int:workflow_id>/event/",
        views.event,
        name="event",
    ),
]

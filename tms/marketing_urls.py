from django.urls import include, path

from properties import views as property_views

urlpatterns = [
    path(
        "p/<str:token>/",
        property_views.public_photo_link,
        name="public_photo_link",
    ),
    path(
        "p/<str:token>/<int:media_id>/",
        property_views.public_photo_file,
        name="public_photo_link_file",
    ),
    path("", include("marketing.urls")),
]

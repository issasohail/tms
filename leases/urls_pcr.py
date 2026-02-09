from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views_pcr import PCRViewSet, PCRPhotoViewSet, PCRVideoViewSet

router = DefaultRouter()
router.register(r"pcr", PCRViewSet, basename="pcr")
router.register(r"pcr-photos", PCRPhotoViewSet, basename="pcr-photo")
router.register(r"pcr-videos", PCRVideoViewSet, basename="pcr-video")

urlpatterns = [
    path("", include(router.urls)),
]

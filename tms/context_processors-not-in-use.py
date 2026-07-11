from django.conf import settings

from ..core.models import GlobalSettings


def global_settings(request):
    return {"global_settings": GlobalSettings.get_solo()}


def environment_settings(request):
    return {
        "APP_ENVIRONMENT": settings.APP_ENVIRONMENT,
        "APP_ENVIRONMENT_LABEL": settings.APP_ENVIRONMENT_LABEL,
    }

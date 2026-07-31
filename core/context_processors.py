from django.conf import settings
from django.core.cache import cache

from .currency import currency_symbol
from .models import GlobalSettings
from .pending_approval_queue import pending_approval_count


def global_settings(request):
    # Public marketing templates have their own explicit context and must not
    # query or expose organization-level TMS settings.
    if getattr(request, "urlconf", None) == "tms.marketing_urls":
        return {}

    settings_obj = cache.get("core.global_settings")

    if settings_obj is None:
        settings_obj = GlobalSettings.get_solo()
        cache.set("core.global_settings", settings_obj, 60)

    approval_count = 0
    if getattr(request.user, "is_authenticated", False):
        approval_count = pending_approval_count()

    return {
        "GLOBAL_SETTINGS": settings_obj,
        "CURRENCY_SYMBOL": currency_symbol(settings_obj),
        "PENDING_APPROVAL_COUNT": approval_count,
        # Environment variables available in every template
        "APP_ENVIRONMENT": getattr(
            settings,
            "APP_ENVIRONMENT",
            "production",
        ),
        "APP_ENVIRONMENT_LABEL": getattr(
            settings,
            "APP_ENVIRONMENT_LABEL",
            "PRODUCTION",
        ),
    }

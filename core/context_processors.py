from django.conf import settings
from django.core.cache import cache

from .currency import currency_symbol
from .models import GlobalSettings
from .pending_approval_queue import pending_approval_count


def _is_settings_embedded_request(request):
    """Return True when a management page is being rendered inside Settings.

    Settings iframes use ``?embed=1``.  This is resolved in the global template
    context processor so every template extending ``base.html`` gets the same
    embedded-layout flag, regardless of which app/view rendered the page.
    """
    value = (request.GET.get("embed") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


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
        request_cache_attr = "_tms_pending_approval_count"
        if hasattr(request, request_cache_attr):
            approval_count = getattr(request, request_cache_attr)
        else:
            approval_count = pending_approval_count(request)
            setattr(request, request_cache_attr, approval_count)

    embedded = _is_settings_embedded_request(request)

    return {
        "GLOBAL_SETTINGS": settings_obj,
        "CURRENCY_SYMBOL": currency_symbol(settings_obj),
        "PENDING_APPROVAL_COUNT": approval_count,
        # A single global flag used by templates/base.html.  Keeping this here
        # makes embedded layout work for every app/page opened by Settings,
        # not only views that remembered to add an ``embed`` context value.
        "tms_embedded": embedded,
        # Compatibility alias for templates/views that already use ``embed``.
        "embed": embedded,
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

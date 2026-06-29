from django.core.cache import cache

from .models import GlobalSettings
from .currency import currency_symbol


def global_settings(request):
    settings_obj = cache.get("core.global_settings")
    if settings_obj is None:
        settings_obj = GlobalSettings.get_solo()
        cache.set("core.global_settings", settings_obj, 60)
    return {
        "GLOBAL_SETTINGS": settings_obj,
        "CURRENCY_SYMBOL": currency_symbol(settings_obj),
    }

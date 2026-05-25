from .models import GlobalSettings
from .currency import currency_symbol

def global_settings(request):
    settings_obj = GlobalSettings.get_solo()
    return {
        "GLOBAL_SETTINGS": settings_obj,
        "CURRENCY_SYMBOL": currency_symbol(settings_obj),
    }

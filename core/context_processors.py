from .models import GlobalSettings

def global_settings(request):
    return {
        "GLOBAL_SETTINGS": GlobalSettings.get_solo()
    }

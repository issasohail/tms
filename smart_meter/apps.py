from django.apps import AppConfig


class SmartMeterConfig(AppConfig):
    name = 'smart_meter'

    def ready(self):
        import smart_meter.models  # registers the signal

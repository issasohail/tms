from django.apps import AppConfig


class SmartMeterConfig(AppConfig):
    name = 'smart_meter'

    def ready(self):
        import smart_meter.models  # noqa: F401 - registers legacy model signals
        import smart_meter.signals  # noqa: F401 - payment-triggered credit evaluation

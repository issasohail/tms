# invoices/apps.py
from django.apps import AppConfig


class InvoicesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'invoices'

    def ready(self):
        # keep empty unless you truly have invoices-specific signals
        pass

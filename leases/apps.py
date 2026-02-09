# leases/apps.py
from django.apps import AppConfig


class LeasesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'leases'  # <-- must match your folder name exactly

    def ready(self):
        """
        Hook: keep the Security Deposit invoice in sync when a Lease is saved.
        Safe to import lazily via apps.get_model to avoid circular imports.
        """
        from django.db.models.signals import post_save
        from django.apps import apps

        # If either model/service is missing during startup/migrations, just skip.
        try:
            Lease = apps.get_model('leases', 'Lease')
            from invoices import services as inv_services
        except Exception:
            return

        def _sync_security(sender, instance, **kwargs):
            try:
                inv_services.ensure_security_deposit_invoice_for(instance)
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "Security invoice sync failed")

        post_save.connect(
            _sync_security,
            sender=Lease,
            dispatch_uid='leases_sync_security_invoice'
        )

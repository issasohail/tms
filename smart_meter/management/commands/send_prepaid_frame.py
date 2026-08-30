# smart_meter/management/commands/send_prepaid_frame.py
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Deprecated: legacy 070104FF writer is permanently disabled."

    def add_arguments(self, parser):
        pass

    def handle(self, *args, **opts):
        raise CommandError(
            "send_prepaid_frame is disabled because its legacy 070104FF encoder is not wire-compatible. "
            "Use the prepaid Parameter 1 UI after review."
        )

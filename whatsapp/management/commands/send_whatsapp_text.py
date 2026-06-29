from django.core.management.base import BaseCommand, CommandError

from whatsapp.services.whatsapp import WhatsAppService


class Command(BaseCommand):
    help = "Send a free-form WhatsApp text message inside an active 24-hour customer window."

    def add_arguments(self, parser):
        parser.add_argument("phone", help="Recipient phone number.")
        parser.add_argument("message", help="Text message to send.")

    def handle(self, *args, **options):
        service = WhatsAppService()
        result = service.send_text(options["phone"], options["message"])
        if not result.get("ok"):
            raise CommandError(result.get("error") or "WhatsApp send failed.")
        self.stdout.write(self.style.SUCCESS(f"WhatsApp text sent. Log ID: {result.get('log_id')}"))

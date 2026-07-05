from django.core.management.base import BaseCommand, CommandError

from whatsapp.services.whatsapp import WhatsAppService


class Command(BaseCommand):
    help = "Send the approved WhatsApp hello_world template to a phone number."

    def add_arguments(self, parser):
        parser.add_argument("phone", help="Recipient phone number.")

    def handle(self, *args, **options):
        service = WhatsAppService()
        result = service.send_template(options["phone"], "hello_world", language_code="en")
        if not result.get("ok"):
            raise CommandError(result.get("error") or "WhatsApp send failed.")
        self.stdout.write(self.style.SUCCESS(f"WhatsApp hello_world sent. Log ID: {result.get('log_id')}"))

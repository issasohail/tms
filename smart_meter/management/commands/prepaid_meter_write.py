from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from smart_meter.services.prepaid_pilot import guarded_parameter_write, PrepaidProtocolSafetyError


class Command(BaseCommand):
    help = "Guarded single-meter prepaid write. Manufacturer verification is mandatory; no --all is supported."

    def add_arguments(self, parser):
        parser.add_argument("--meter-id", type=int, required=True)
        parser.add_argument("--parameter", required=True)
        parser.add_argument("--value", required=True)
        parser.add_argument("--confirm-meter-number", required=True)
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--reason", required=True)

    def handle(self, *args, **opts):
        user = get_user_model().objects.get(pk=opts["user_id"])
        try:
            guarded_parameter_write(
                meter_id=opts["meter_id"], parameter=opts["parameter"], value=opts["value"],
                user=user, reason=opts["reason"], confirm_meter_number=opts["confirm_meter_number"],
            )
        except PrepaidProtocolSafetyError as exc:
            raise CommandError(str(exc))

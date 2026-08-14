from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "List recently created user accounts without modifying them."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            raise CommandError("--days must be at least 1.")

        cutoff = timezone.now() - timedelta(days=days)
        users = (
            get_user_model()
            .objects.filter(date_joined__gte=cutoff)
            .prefetch_related("groups")
            .order_by("-date_joined", "username")
        )
        self.stdout.write(
            "username\tdate_joined\tlast_login\tactive\tstaff\tsuperuser\tgroups"
        )
        for user in users:
            groups = ", ".join(user.groups.values_list("name", flat=True)) or "-"
            self.stdout.write(
                "\t".join(
                    [
                        user.username,
                        user.date_joined.isoformat() if user.date_joined else "-",
                        user.last_login.isoformat() if user.last_login else "-",
                        str(user.is_active),
                        str(user.is_staff),
                        str(user.is_superuser),
                        groups,
                    ]
                )
            )
        self.stdout.write(self.style.SUCCESS(f"Listed {users.count()} user(s)."))

import sys

from django.conf import settings
from django.core.management.base import BaseCommand
from redis import Redis
from rq import SimpleWorker


class Command(BaseCommand):
    help = "Run the local RQ worker for monthly billing jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--queue",
            default=getattr(settings, "BILLING_RQ_QUEUE", "billing"),
            help="RQ queue name. Defaults to settings.BILLING_RQ_QUEUE.",
        )

    def handle(self, *args, **options):
        redis_url = getattr(
            settings, "BILLING_RQ_REDIS_URL", "redis://localhost:6379/1"
        )
        connection = Redis.from_url(redis_url)
        worker_cls = (
            SimpleWorker if sys.platform == "win32" else __import__("rq").Worker
        )
        worker = worker_cls([options["queue"]], connection=connection)
        self.stdout.write(
            self.style.SUCCESS(
                f"Starting billing RQ worker ({worker_cls.__name__}) on queue '{options['queue']}' using {redis_url}"
            )
        )
        worker.work()

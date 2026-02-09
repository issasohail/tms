import sys
import os
import time
from django.core.management.base import BaseCommand

# ✅ Add your project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

class Command(BaseCommand):
    help = "Continuously poll all smart meters every 60 seconds."

    def handle(self, *args, **options):
        # ✅ Import AFTER fixing sys.path, inside method
        from smart_meter.tasks import poll_all_meters

        self.stdout.write(self.style.SUCCESS(
            "🔁 Starting smart meter polling..."))

        try:
            while True:
                self.stdout.write("📡 Polling all smart meters...")
                poll_all_meters()
                time.sleep(60)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("❌ Polling stopped by user."))

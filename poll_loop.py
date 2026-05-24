from tasks import poll_all_meters  # <-- tasks is now accessible
import django
import os
import sys
import time

# ✅ Add your smart_meter directory directly to path
sys.path.append(os.path.abspath(os.path.join(
    os.path.dirname(__file__), "smart_meter")))

# ✅ Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE",
                      "tenant_management_system.settings")
django.setup()

# ✅ Import after setup

print("🔁 Starting smart meter polling loop...")

try:
    while True:
        print("📡 Polling all smart meters...")
        poll_all_meters()
        time.sleep(60)
except KeyboardInterrupt:
    print("❌ Polling stopped by user.")

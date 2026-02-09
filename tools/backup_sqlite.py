import os
import sqlite3
import datetime
import glob
from pathlib import Path

# TODO: change if your Django project module isn't "tms"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tms.settings")

# If you want to infer DB path without Django, set this directly:
# DB_PATH = r"C:\tenant_management_system\db.sqlite3"

try:
    import django
    django.setup()
    from django.conf import settings
    DB_PATH = settings.DATABASES["default"]["NAME"]
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
except Exception:
    # Fallback (uncomment and set a hard path if needed)
    # DB_PATH = r"C:\tenant_management_system\db.sqlite3"
    raise

BACKUP_DIR = Path(r"C:\backups\tms")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
backup_path = BACKUP_DIR / f"db-{ts}.sqlite3"

with sqlite3.connect(DB_PATH) as src, sqlite3.connect(backup_path) as dst:
    src.backup(dst)  # online, consistent snapshot

# Keep only last 14 days of backups
keep_days = 14
cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
for f in BACKUP_DIR.glob("db-*.sqlite3"):
    try:
        dt = datetime.datetime.strptime(f.stem[3:], "%Y%m%d-%H%M%S")
        if dt < cutoff:
            f.unlink()
    except Exception:
        pass

print("Backup written:", backup_path)

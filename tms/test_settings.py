from .settings import *  # noqa: F401,F403


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "test_sqlite.sqlite3",  # noqa: F405
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]


class DisableMigrations(dict):
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = DisableMigrations()

WHATSAPP_AI_USE_CELERY = False
BILLING_RQ_REDIS_URL = "redis://localhost:6379/1"
BILLING_RQ_QUEUE = "billing"

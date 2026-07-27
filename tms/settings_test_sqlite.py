"""Local test settings used when the shared MySQL test database is unavailable."""

from .settings import *  # noqa: F403


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# The legacy migration chain contains database-specific duplicate-column SQL.
# Test directly from the current model state so local SQLite runs stay isolated
# from those historical production migrations.
MIGRATION_MODULES = {
    app_label: None
    for app_label in (
        "accounts",
        "properties",
        "core",
        "tenants",
        "expenses",
        "documents",
        "smart_meter",
        "notifications",
        "leases",
        "reports",
        "invoices",
        "utilities",
        "payments",
        "maintenance",
        "handyman",
        "whatsapp",
    )
}

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0026_tenant_cnic_validity_dates"),
    ]

    # NOTE (fixed 2026-08-06): This migration originally re-added
    # temporary_address_urdu / permanent_address_urdu via raw SQL, but
    # migration 0026 (AddField) already creates these columns through the
    # ORM. Running this migration's raw SQL on any fresh database (a new
    # dev machine, staging server, or disaster-recovery restore) fails with
    # "duplicate column name" because 0026 already added them. On an
    # already-migrated production database this migration is a no-op
    # (columns already exist / already NOT NULL with default ''), so
    # converting it to RunSQL.noop is safe and does not touch existing data.
    operations = [
        migrations.RunSQL(
            sql=migrations.RunSQL.noop,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]

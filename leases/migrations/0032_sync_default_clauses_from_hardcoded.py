from importlib import import_module

from django.db import migrations


def sync_default_clauses(apps, schema_editor):
    DefaultClause = apps.get_model("leases", "DefaultClause")
    source = import_module("leases.migrations.0031_seed_default_clauses")

    for clause_number, body in enumerate(source.HARDCODED_DEFAULT_CLAUSES, start=1):
        clause = DefaultClause.objects.filter(
            clause_number=clause_number,
            is_active=True,
        ).first()

        if clause:
            clause.body = body
            clause.save(update_fields=["body", "updated_at"])
            continue

        DefaultClause.objects.create(
            clause_number=clause_number,
            body=body,
            is_active=True,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0031_seed_default_clauses"),
    ]

    operations = [
        migrations.RunPython(sync_default_clauses, migrations.RunPython.noop),
    ]

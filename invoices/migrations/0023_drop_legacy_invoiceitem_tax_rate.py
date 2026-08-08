from django.db import migrations


def drop_legacy_tax_rate_column(apps, schema_editor):
    """Drop the obsolete physical InvoiceItem.tax_rate column if it still exists.

    Migration 0002 removed ``tax_rate`` from Django's migration state only,
    assuming the database column had already been removed.  On a fresh
    database, however, 0001 creates the column and 0002 leaves it in place.
    That causes MySQL inserts through the current InvoiceItem model to fail
    because the stale NOT NULL column is not part of the Django model.

    This forward repair is intentionally database-only and idempotent: it
    removes the stale column when present and does nothing when the database
    is already correct.
    """
    table_name = "invoices_invoiceitem"
    connection = schema_editor.connection

    existing_tables = set(connection.introspection.table_names())
    if table_name not in existing_tables:
        return

    with connection.cursor() as cursor:
        columns = {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, table_name
            )
        }

    if "tax_rate" not in columns:
        return

    qn = schema_editor.quote_name
    schema_editor.execute(
        f"ALTER TABLE {qn(table_name)} DROP COLUMN {qn('tax_rate')}"
    )


class Migration(migrations.Migration):
    # MySQL DDL can commit implicitly, so keep this repair outside an atomic
    # migration transaction.
    atomic = False

    dependencies = [
        ("invoices", "0022_reconcile_fully_paid_invoice_statuses"),
    ]

    operations = [
        migrations.RunPython(
            drop_legacy_tax_rate_column,
            reverse_code=migrations.RunPython.noop,
        ),
    ]

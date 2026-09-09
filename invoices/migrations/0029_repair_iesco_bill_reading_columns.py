from django.db import migrations


def add_missing_iesco_bill_reading_columns(apps, schema_editor):
    """Repair databases where migration 0028 was recorded without all columns."""
    model = apps.get_model("invoices", "IescoBillReading")
    table_name = model._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        existing_columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, table_name
            )
        }

    for field_name in ("bill_history", "current_month_paid"):
        field = model._meta.get_field(field_name)
        if field.column not in existing_columns:
            schema_editor.add_field(model, field)
            existing_columns.add(field.column)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("invoices", "0028_iescobillreading"),
    ]

    operations = [
        migrations.RunPython(
            add_missing_iesco_bill_reading_columns,
            reverse_code=migrations.RunPython.noop,
        ),
    ]

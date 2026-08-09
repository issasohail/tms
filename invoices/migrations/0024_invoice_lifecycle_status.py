from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def initialize_lifecycle_status(apps, schema_editor):
    Invoice = apps.get_model("invoices", "Invoice")
    Invoice.objects.filter(status="draft").update(lifecycle_status="draft")
    Invoice.objects.filter(status="cancelled").update(lifecycle_status="cancelled")


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("invoices", "0023_drop_legacy_invoiceitem_tax_rate"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="lifecycle_status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("issued", "Issued"),
                    ("disputed", "Disputed"),
                    ("cancelled", "Cancelled"),
                    ("void", "Void"),
                    ("written_off", "Written Off"),
                ],
                db_index=True,
                default="issued",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="lifecycle_status_reason",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="invoice",
            name="lifecycle_status_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="lifecycle_status_updated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="invoice_lifecycle_updates",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="InvoiceStatusHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("previous_status", models.CharField(blank=True, default="", max_length=20)),
                ("new_status", models.CharField(max_length=20)),
                ("reason", models.CharField(blank=True, default="", max_length=255)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("changed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="invoice_status_history_changes", to=settings.AUTH_USER_MODEL)),
                ("invoice", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="status_history", to="invoices.invoice")),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.RunPython(initialize_lifecycle_status, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name="invoice",
            options={
                "ordering": ["-issue_date"],
                "permissions": [
                    ("change_invoice_lifecycle_status", "Can change invoice lifecycle status"),
                    ("cancel_invoice", "Can cancel invoice"),
                    ("void_invoice", "Can void invoice"),
                    ("write_off_invoice", "Can write off invoice"),
                    ("view_invoice_status_history", "Can view invoice status history"),
                ],
            },
        ),
    ]

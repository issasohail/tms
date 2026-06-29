from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("invoices", "0014_securitydeposittransaction_deduction_amount_and_more"),
        ("leases", "0047_defaultclause_category_and_more"),
        ("properties", "0015_alter_propertymedia_file_alter_unitmedia_file"),
        ("tenants", "0017_alter_tenant_updated_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="MonthlyBillingRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("billing_month", models.DateField(help_text="First day of the month being billed.")),
                ("run_date", models.DateField(default=django.utils.timezone.localdate)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("preflight", "Preflight"), ("generating", "Generating"), ("ready", "Ready"), ("partial", "Partial"), ("sent", "Sent"), ("failed", "Failed")], default="draft", max_length=20)),
                ("total_active_leases", models.PositiveIntegerField(default=0)),
                ("recurring_created_count", models.PositiveIntegerField(default=0)),
                ("missing_recurring_count", models.PositiveIntegerField(default=0)),
                ("electric_ready_count", models.PositiveIntegerField(default=0)),
                ("electric_pending_count", models.PositiveIntegerField(default=0)),
                ("water_missing_count", models.PositiveIntegerField(default=0)),
                ("ready_to_send_count", models.PositiveIntegerField(default=0)),
                ("pending_attention_count", models.PositiveIntegerField(default=0)),
                ("sent_count", models.PositiveIntegerField(default=0)),
                ("failed_count", models.PositiveIntegerField(default=0)),
                ("skipped_count", models.PositiveIntegerField(default=0)),
                ("created_by_label", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="monthly_billing_runs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-billing_month", "-created_at"],
                "indexes": [models.Index(fields=["billing_month", "status"], name="invoices_mo_billing_e96700_idx")],
            },
        ),
        migrations.CreateModel(
            name="MonthlyBillingRunItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("draft", "Draft"), ("ready_to_send", "Ready to Send"), ("pending_attention", "Pending Attention"), ("sent", "Sent"), ("failed", "Failed"), ("skipped", "Skipped")], default="draft", max_length=30)),
                ("issue_code", models.CharField(blank=True, choices=[("inactive_lease", "Inactive lease"), ("missing_recurring_invoice_setup", "Missing recurring invoice setup"), ("recurring_invoice_generation_failed", "Recurring invoice generation failed"), ("duplicate_invoice_exists", "Duplicate invoice exists"), ("latest_meter_reading_missing", "Latest meter reading missing"), ("meter_offline", "Meter offline"), ("electric_billing_not_verified", "Electric billing not verified"), ("water_charge_missing", "Water charge missing"), ("tenant_phone_missing", "Tenant phone missing"), ("pdf_generation_failed", "PDF generation failed"), ("whatsapp_send_failed", "WhatsApp send failed"), ("unusual_invoice_total", "Unusual invoice total"), ("zero_invoice_total", "Zero invoice total")], max_length=80)),
                ("issue_message", models.TextField(blank=True)),
                ("recurring_invoice_found", models.BooleanField(default=False)),
                ("recurring_invoice_created", models.BooleanField(default=False)),
                ("electric_required", models.BooleanField(default=False)),
                ("electric_ready", models.BooleanField(default=False)),
                ("electric_charge", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("latest_meter_reading_date", models.DateField(blank=True, null=True)),
                ("water_required", models.BooleanField(default=False)),
                ("water_charge", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("water_resolved", models.BooleanField(default=False)),
                ("invoice_total", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("invoice_pdf", models.FileField(blank=True, max_length=255, null=True, upload_to="invoices/monthly_billing_pdfs/")),
                ("whatsapp_message_id", models.CharField(blank=True, max_length=160)),
                ("whatsapp_status", models.CharField(blank=True, max_length=30)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("error_text", models.TextField(blank=True)),
                ("log", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("billing_run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="invoices.monthlybillingrun")),
                ("invoice", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="monthly_billing_items", to="invoices.invoice")),
                ("lease", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="monthly_billing_items", to="leases.lease")),
                ("property", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="properties.property")),
                ("tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="tenants.tenant")),
                ("unit", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="properties.unit")),
            ],
            options={
                "ordering": ["billing_run", "property_id", "unit_id", "lease_id"],
                "indexes": [
                    models.Index(fields=["billing_run", "status"], name="invoices_mo_billing_c9c4e3_idx"),
                    models.Index(fields=["lease", "status"], name="invoices_mo_lease_i_529356_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="monthlybillingrunitem",
            constraint=models.UniqueConstraint(fields=("billing_run", "lease"), name="uniq_monthly_billing_run_lease"),
        ),
    ]

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def convert_legacy_transfers(apps, schema_editor):
    SecurityDepositTransaction = apps.get_model("invoices", "SecurityDepositTransaction")
    SecurityDepositLedgerTransfer = apps.get_model("invoices", "SecurityDepositLedgerTransfer")
    rows = SecurityDepositTransaction.objects.filter(
        type="REFUND",
        refund_status="PAID",
        amount=0,
        deduction_amount__gt=0,
        notes__icontains="transferred to the lease ledger",
    )
    for row in rows.iterator():
        row.refund_status = "TRANSFERRED"
        row.save(update_fields=["refund_status"])
        if row.payment_id:
            SecurityDepositLedgerTransfer.objects.get_or_create(
                security_movement_id=row.pk,
                defaults={
                    "lease_id": row.lease_id,
                    "amount": row.deduction_amount,
                    "transaction_date": row.date,
                    "reason": "Legacy security deposit transfer to tenant ledger",
                    "reference": f"LEGACY-SECLEDGER-{row.pk}",
                    "ledger_credit_payment_id": row.payment_id,
                },
            )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("invoices", "0024_invoice_lifecycle_status"),
        ("payments", "0010_rename_allocation_relations_to_payment_detail"),
    ]

    operations = [
        migrations.AlterField(
            model_name="securitydeposittransaction",
            name="refund_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("PENDING", "Pending"),
                    ("APPROVED", "Approved"),
                    ("PAID", "Paid"),
                    ("TRANSFERRED", "Transferred to Ledger"),
                    ("CANCELLED", "Cancelled"),
                ],
                default="PAID",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="SecurityDepositLedgerTransfer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("transaction_date", models.DateField(default=django.utils.timezone.localdate)),
                ("reason", models.CharField(max_length=255)),
                ("reference", models.CharField(max_length=80, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reversed_at", models.DateTimeField(blank=True, null=True)),
                ("reversal_reason", models.CharField(blank=True, default="", max_length=255)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="security_ledger_transfers_created", to=settings.AUTH_USER_MODEL)),
                ("lease", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="security_ledger_transfers", to="leases.lease")),
                ("ledger_credit_payment", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="security_ledger_transfer_credit", to="payments.payment")),
                ("reversal_payment", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="security_ledger_transfer_reversal", to="payments.payment")),
                ("reversed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="security_ledger_transfers_reversed", to=settings.AUTH_USER_MODEL)),
                ("security_movement", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="ledger_transfer_event", to="invoices.securitydeposittransaction")),
            ],
            options={
                "ordering": ("-transaction_date", "-id"),
                "permissions": [
                    ("transfer_security_deposit_to_ledger", "Can transfer refundable security deposit to ledger"),
                    ("reverse_security_deposit_ledger_transfer", "Can reverse security deposit ledger transfer"),
                ],
            },
        ),
        migrations.RunPython(convert_legacy_transfers, migrations.RunPython.noop),
    ]

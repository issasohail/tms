from django.db import migrations


def backfill_missing_security_transactions(apps, schema_editor):
    Payment = apps.get_model("payments", "Payment")
    PaymentAllocation = apps.get_model("payments", "PaymentAllocation")
    SecurityDepositTransaction = apps.get_model("invoices", "SecurityDepositTransaction")

    existing_allocation_ids = set(
        SecurityDepositTransaction.objects.exclude(allocation_id__isnull=True)
        .values_list("allocation_id", flat=True)
    )

    allocations = PaymentAllocation.objects.filter(
        security_amount__gt=0,
    ).exclude(
        id__in=existing_allocation_ids,
    )

    for allocation in allocations.iterator():
        if not allocation.payment_id:
            continue

        payment = Payment.objects.filter(pk=allocation.payment_id).first()
        if not payment or not payment.lease_id:
            continue

        SecurityDepositTransaction.objects.create(
            allocation_id=allocation.id,
            payment_id=payment.id,
            lease_id=payment.lease_id,
            date=payment.payment_date,
            type=(allocation.security_type or "PAYMENT").upper(),
            amount=allocation.security_amount,
            notes=payment.notes or f"Backfilled from payment allocation #{allocation.id}",
        )


def noop_reverse(apps, schema_editor):
    # Keep production data intact if this migration is rolled back.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("invoices", "0019_alter_invoiceitem_description_length"),
        ("payments", "0006_alter_allocationauditlog_changed_by_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_missing_security_transactions, noop_reverse),
    ]

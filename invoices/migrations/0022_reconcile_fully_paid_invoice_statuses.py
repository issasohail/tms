from collections import defaultdict
from decimal import Decimal

from django.db import migrations


ZERO = Decimal("0.00")
BATCH_SIZE = 500


def reconcile_fully_paid_invoice_statuses(apps, schema_editor):
    """Mark invoices fully covered by lease payments, oldest invoice first."""
    Invoice = apps.get_model("invoices", "Invoice")
    Payment = apps.get_model("payments", "Payment")
    PaymentDetail = apps.get_model("payments", "PaymentDetail")

    payment_rows = list(Payment.objects.values_list("id", "lease_id", "amount"))
    payment_ids = [payment_id for payment_id, _lease_id, _amount in payment_rows]
    lease_amount_by_payment = dict(
        PaymentDetail.objects.filter(payment_id__in=payment_ids).values_list(
            "payment_id", "lease_amount"
        )
    )
    available_by_lease = defaultdict(lambda: ZERO)
    for payment_id, lease_id, amount in payment_rows:
        available_by_lease[lease_id] += (
            lease_amount_by_payment.get(payment_id, amount) or ZERO
        )

    covered_ids = []
    blocked_leases = set()
    invoices = (
        Invoice.objects.exclude(status="cancelled")
        .order_by("lease_id", "issue_date", "id")
        .values_list("id", "lease_id", "amount", "status")
    )
    for invoice_id, lease_id, amount, status in invoices.iterator():
        if lease_id in blocked_leases:
            continue
        amount = amount or ZERO
        if amount <= ZERO:
            continue
        if available_by_lease[lease_id] < amount:
            blocked_leases.add(lease_id)
            continue
        available_by_lease[lease_id] -= amount
        if status != "paid":
            covered_ids.append(invoice_id)

    for offset in range(0, len(covered_ids), BATCH_SIZE):
        Invoice.objects.filter(
            pk__in=covered_ids[offset : offset + BATCH_SIZE]
        ).update(status="paid")


class Migration(migrations.Migration):
    dependencies = [
        ("invoices", "0021_rename_allocation_to_payment_detail"),
        ("payments", "0010_rename_allocation_relations_to_payment_detail"),
    ]

    operations = [
        migrations.RunPython(
            reconcile_fully_paid_invoice_statuses,
            reverse_code=migrations.RunPython.noop,
        ),
    ]

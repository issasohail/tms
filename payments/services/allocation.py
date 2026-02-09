# payments/services/allocation.py
from __future__ import annotations

from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from payments.models import PaymentAllocation
from invoices.models import SecurityDepositTransaction


def D(v) -> Decimal:
    try:
        return Decimal(v or "0")
    except Exception:
        return Decimal("0")


# payments/services/allocation.py

@transaction.atomic
def rebuild_allocation(*, payment, lease_amount, security_amount, security_type="PAYMENT", user=None, reason=""):
    lease_amt = D(lease_amount)
    sec_amt   = D(security_amount)
    sec_type  = (security_type or "PAYMENT").upper()

    total = lease_amt + sec_amt

    # Keep payment cash consistent with allocation sum
    payment.amount = total
    payment.save(update_fields=["amount"])

    # ALWAYS upsert allocation
    alloc, _ = PaymentAllocation.objects.update_or_create(
        payment=payment,
        defaults=dict(
            lease_amount=lease_amt,
            security_amount=sec_amt,
            security_type=sec_type,
            updated_by=user if user and getattr(user, "is_authenticated", False) else None,
            last_reason=reason or "",
        ),
    )

    # Security ledger row ONLY when sec_amt > 0
    if sec_amt > 0:
        SecurityDepositTransaction.objects.update_or_create(
            allocation=alloc,
            defaults=dict(
                lease=payment.lease,
                payment=payment,
                date=payment.payment_date,
                type=sec_type,
                amount=sec_amt,
                notes=payment.notes or "",
            ),
        )
    else:
        SecurityDepositTransaction.objects.filter(allocation=alloc).delete()

    return alloc

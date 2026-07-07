from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from invoices.models import SecurityDepositTransaction
from payments.models import PaymentDetail


def D(v) -> Decimal:
    try:
        return Decimal(v or "0")
    except Exception:
        return Decimal("0")


@transaction.atomic
def rebuild_payment_detail(*, payment, lease_amount, security_amount, security_type="PAYMENT", user=None, reason=""):
    lease_amt = D(lease_amount)
    sec_amt = D(security_amount)
    sec_type = (security_type or "PAYMENT").upper()

    if sec_amt < 0:
        raise ValueError("Security payment detail amount cannot be negative.")

    total = lease_amt + sec_amt
    if payment.amount != total:
        payment.amount = total
        payment.save(update_fields=["amount"])

    detail, _ = PaymentDetail.objects.update_or_create(
        payment=payment,
        defaults={
            "lease_amount": lease_amt,
            "security_amount": sec_amt,
            "security_type": sec_type,
            "updated_by": user if user and getattr(user, "is_authenticated", False) else None,
            "last_reason": reason or "",
        },
    )

    SecurityDepositTransaction.objects.filter(payment=payment).exclude(payment_detail=detail).delete()

    if sec_amt > 0:
        SecurityDepositTransaction.objects.update_or_create(
            payment_detail=detail,
            defaults={
                "lease": payment.lease,
                "payment": payment,
                "date": payment.payment_date,
                "type": sec_type,
                "amount": sec_amt,
                "notes": payment.notes or "",
            },
        )
    else:
        SecurityDepositTransaction.objects.filter(payment_detail=detail).delete()

    return detail

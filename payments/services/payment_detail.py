from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Case, DecimalField, Exists, F, OuterRef, When

from invoices.models import SecurityDepositTransaction
from payments.models import PaymentDetail


def D(v) -> Decimal:
    try:
        return Decimal(v or "0")
    except Exception:
        return Decimal("0")


def lease_payment_amount_expression():
    """Lease portion, treating a removed security movement as lease money."""
    movement_exists = Exists(
        SecurityDepositTransaction.objects.filter(payment_id=OuterRef("pk"))
        | SecurityDepositTransaction.objects.filter(
            payment_detail_id=OuterRef("detail__pk")
        )
    )
    money_field = DecimalField(max_digits=12, decimal_places=2)
    return Case(
        When(
            detail__security_amount__gt=0,
            then=Case(
                When(movement_exists, then=F("detail__lease_amount")),
                default=F("amount"),
                output_field=money_field,
            ),
        ),
        When(detail__isnull=False, then=F("detail__lease_amount")),
        default=F("amount"),
        output_field=money_field,
    )


def sync_security_deposit_paid_flag(lease):
    required = D(lease.security_deposit)
    paid_in = sum(
        SecurityDepositTransaction.objects.filter(
            lease=lease,
            type="PAYMENT",
        ).values_list("amount", flat=True),
        Decimal("0.00"),
    )
    is_paid = required > 0 and paid_in >= required
    if lease.security_deposit_paid != is_paid:
        lease.security_deposit_paid = is_paid
        lease.save(update_fields=["security_deposit_paid"])
    return is_paid


@transaction.atomic
def rebuild_payment_detail(
    *,
    payment,
    lease_amount,
    security_amount,
    electricity_amount=Decimal("0.00"),
    electricity_meter=None,
    security_type="PAYMENT",
    user=None,
    reason="",
):
    lease_amt = D(lease_amount)
    sec_amt = D(security_amount)
    electric_amt = D(electricity_amount)
    sec_type = (security_type or "PAYMENT").upper()

    if sec_amt < 0:
        raise ValueError("Security payment detail amount cannot be negative.")
    if electric_amt < 0 or electric_amt > max(lease_amt, Decimal("0.00")):
        raise ValueError("Electricity allocation must be between zero and the positive lease amount.")
    if electric_amt > 0 and electricity_meter is None:
        raise ValueError("An electricity meter is required for an electricity allocation.")
    if electricity_meter is not None:
        from smart_meter.models import MeterInstallation

        if not MeterInstallation.objects.filter(
            lease=payment.lease,
            meter=electricity_meter,
        ).exists():
            raise ValueError("The electricity meter is not linked to this payment's lease.")

    signed_security = -sec_amt if sec_type == "REFUND" else sec_amt
    total = lease_amt + signed_security
    if payment.amount != total:
        payment.amount = total
        payment.save(update_fields=["amount"])

    detail, _ = PaymentDetail.objects.update_or_create(
        payment=payment,
        defaults={
            "lease_amount": lease_amt,
            "security_amount": sec_amt,
            "electricity_amount": electric_amt,
            "electricity_meter": electricity_meter if electric_amt > 0 else None,
            "security_type": sec_type,
            "updated_by": user if user and getattr(user, "is_authenticated", False) else None,
            "last_reason": reason or "",
        },
    )

    SecurityDepositTransaction.objects.filter(payment=payment).exclude(payment_detail=detail).delete()

    if sec_amt > 0:
        pending_refund = None
        if sec_type == "REFUND":
            pending_refund = (
                SecurityDepositTransaction.objects.select_for_update()
                .filter(
                    lease=payment.lease,
                    type="REFUND",
                    refund_status="PENDING",
                    payment__isnull=True,
                    payment_detail__isnull=True,
                    amount=sec_amt,
                )
                .order_by("id")
                .first()
            )
        if pending_refund:
            pending_refund.payment_detail = detail
            pending_refund.payment = payment
            pending_refund.date = payment.payment_date
            pending_refund.refund_status = "PAID"
            pending_refund.notes = "\n".join(
                filter(None, [pending_refund.notes, payment.notes or "Refund payment posted."])
            )
            pending_refund.save(
                update_fields=[
                    "payment_detail",
                    "payment",
                    "date",
                    "refund_status",
                    "notes",
                ]
            )
        else:
            SecurityDepositTransaction.objects.update_or_create(
                payment_detail=detail,
                defaults={
                    "lease": payment.lease,
                    "payment": payment,
                    "date": payment.payment_date,
                    "type": sec_type,
                    "amount": sec_amt,
                    "refund_status": "PAID",
                    "notes": payment.notes or "",
                },
            )
    else:
        SecurityDepositTransaction.objects.filter(payment_detail=detail).delete()

    sync_security_deposit_paid_flag(payment.lease)

    return detail

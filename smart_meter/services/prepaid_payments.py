"""Create one durable prepaid recharge from one electricity payment allocation."""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db import IntegrityError, transaction

from payments.models import PaymentDetail
from smart_meter.models import MeterPrepaidPaymentTopup
from smart_meter.services.prepaid_money import queue_prepaid_money_transaction
from smart_meter.services.prepaid_pilot import (
    prepaid_allowlisted,
    prepaid_payment_topups_enabled,
    prepaid_writes_enabled,
)

logger = logging.getLogger(__name__)


def queue_payment_electricity_topup(payment_detail_id: int):
    detail = (
        PaymentDetail.objects.select_related("payment", "electricity_meter")
        .filter(pk=payment_detail_id)
        .first()
    )
    if detail is None:
        return None
    amount = Decimal(detail.electricity_amount or "0.00").quantize(Decimal("0.01"))
    meter = detail.electricity_meter
    if amount <= 0 or meter is None:
        return None

    try:
        with transaction.atomic():
            record, created = MeterPrepaidPaymentTopup.objects.get_or_create(
                source_payment_detail_id=detail.pk,
                defaults={
                    "payment_detail": detail,
                    "meter": meter,
                    "allocated_amount": amount,
                },
            )
    except IntegrityError:
        record = MeterPrepaidPaymentTopup.objects.get(source_payment_detail_id=detail.pk)
        created = False
    if not created:
        return record

    error = ""
    if not prepaid_payment_topups_enabled():
        error = "Automatic prepaid payment top-ups are disabled in Meter Settings."
    elif not prepaid_writes_enabled():
        error = "Prepaid writes are disabled in Meter Settings."
    elif not prepaid_allowlisted(meter):
        error = "The allocated meter is not enabled for prepaid operations."
    if error:
        record.status = "failed"
        record.error = error
        record.save(update_fields=["status", "error", "updated_at"])
        return record

    try:
        recharge, _command = queue_prepaid_money_transaction(
            meter=meter,
            operation="recharge",
            amount=amount,
            initiated_by=(
                detail.updated_by.get_username() if detail.updated_by_id else "payment-allocation"
            ),
            reason=f"Electricity allocation from payment #{detail.payment_id}",
        )
    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)
        record.save(update_fields=["status", "error", "updated_at"])
        logger.exception("Unable to queue prepaid payment top-up detail=%s", detail.pk)
        return record

    record.recharge = recharge
    record.status = recharge.status if recharge.status in {"verified", "uncertain", "failed"} else "queued"
    record.error = recharge.reconciliation_note if record.status in {"uncertain", "failed"} else ""
    record.save(update_fields=["recharge", "status", "error", "updated_at"])
    return record

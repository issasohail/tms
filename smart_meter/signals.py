"""Small accounting integration hooks for meter-credit recalculation.

They enqueue/recalculate only after the payment transaction commits.  No relay command
is issued directly from a payment model signal.
"""
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from payments.models import Payment, PaymentDetail


def _enqueue_for_lease(lease_id):
    from smart_meter.models import MeterCreditAccount, MeterEvaluationRequest
    for account in MeterCreditAccount.objects.filter(lease_id=lease_id, is_enabled=True).select_related("meter"):
        pending = MeterEvaluationRequest.objects.filter(meter=account.meter, status="pending").first()
        if pending:
            continue
        MeterEvaluationRequest.objects.create(meter=account.meter, status="pending")


@receiver(post_save, sender=Payment, dispatch_uid="smart_meter_credit_payment_saved")
def payment_saved(sender, instance, **kwargs):
    lease_id = instance.lease_id
    transaction.on_commit(lambda: _enqueue_for_lease(lease_id))


@receiver(post_delete, sender=Payment, dispatch_uid="smart_meter_credit_payment_deleted")
def payment_deleted(sender, instance, **kwargs):
    lease_id = instance.lease_id
    transaction.on_commit(lambda: _enqueue_for_lease(lease_id))


@receiver(post_save, sender=PaymentDetail, dispatch_uid="smart_meter_credit_payment_detail_saved")
def payment_detail_saved(sender, instance, **kwargs):
    payment = instance.payment
    if payment_id := getattr(instance, "payment_id", None):
        lease_id = payment.lease_id if payment else Payment.objects.filter(pk=payment_id).values_list("lease_id", flat=True).first()
        if lease_id:
            transaction.on_commit(lambda: _enqueue_for_lease(lease_id))


@receiver(post_delete, sender=PaymentDetail, dispatch_uid="smart_meter_credit_payment_detail_deleted")
def payment_detail_deleted(sender, instance, **kwargs):
    payment = instance._state.fields_cache.get("payment")
    lease_id = getattr(payment, "lease_id", None)
    if not lease_id and instance.payment_id:
        lease_id = (
            Payment.objects.filter(pk=instance.payment_id)
            .values_list("lease_id", flat=True)
            .first()
        )
    if lease_id:
        transaction.on_commit(lambda: _enqueue_for_lease(lease_id))

def rebuild_allocation(payment, lease_amount, security_amount, security_type, user=None):
    old = None
    if hasattr(payment, "allocation"):
        old = {
            "lease": str(payment.allocation.lease_amount),
            "security": str(payment.allocation.security_amount),
            "type": payment.allocation.security_type,
        }
        payment.allocation.delete()

    alloc = PaymentAllocation.objects.create(
        payment=payment,
        lease_amount=lease_amount,
        security_amount=security_amount,
        security_type=security_type,
    )

    AllocationAuditLog.objects.create(
        allocation=alloc,
        changed_by=user,
        old_data=old or {},
        new_data={
            "lease": str(lease_amount),
            "security": str(security_amount),
            "type": security_type,
        }
    )

    # rebuild security tx
    SecurityDepositTransaction.objects.filter(payment=payment).delete()

    if security_amount > 0:
        SecurityDepositTransaction.objects.create(
            lease=payment.lease,
            payment=payment,
            amount=security_amount,
            type=security_type,
        )

    return alloc

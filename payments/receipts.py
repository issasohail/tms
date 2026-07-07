from decimal import Decimal

def _fmt(x):
    x = Decimal(x or 0)
    return f"{x:,.2f}"

def build_payment_detail_receipt_message(request, payment_detail, lease_balance, security_balance):
    lease = payment_detail.lease
    tenant = lease.tenant
    unit = lease.unit
    prop = unit.property

    lines = [
        f"Dear {getattr(tenant,'first_name','') or 'Customer'},",
        "",
        f"Payment received for {prop.property_name}.",
        f"Unit: {unit.unit_number}",
        f"Date: {payment_detail.payment_date:%b %d, %Y}",
        f"Method: {payment_detail.payment_method.name if payment_detail.payment_method else ''}".strip(),
    ]

    lines.append("")
    lines.append(f"*Total Received: Rs. {_fmt(payment_detail.total_received)}*")
    lines.append("Breakdown:")
    if payment_detail.lease_amount > 0:
        lines.append(f"- Lease Payment: Rs. {_fmt(payment_detail.lease_amount)}")
    if payment_detail.security_amount > 0:
        st = payment_detail.security_type or "PAYMENT"
        lines.append(f"- Security Deposit ({st}): Rs. {_fmt(payment_detail.security_amount)}")

    lines.append("")
    lines.append(f"Lease Balance: Rs. {_fmt(lease_balance)}")
    lines.append(f"Security Balance: Rs. {_fmt(security_balance)}")
    lines.append(f"*Total Balance: Rs. {_fmt(Decimal(lease_balance) + Decimal(security_balance))}*")
    lines.append("")
    lines.append("Thank you!")

    return "\n".join([l for l in lines if l is not None])

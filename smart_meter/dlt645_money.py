# smart_meter/dlt645_money.py
from __future__ import annotations

from smart_meter.dlt645 import (
    build_init_amount_frame as _build_init_amount_frame,
    build_charge_frame,
)

def build_amount_init_frame(
    addr12: str,
    amount_rupees: float,
    *,
    operator: bytes,
    mac1: bytes,
    purchase_count: bytes,
    mac2: bytes,
    checksum_mode: str,
) -> bytes:
    """Canonical FE-prefixed 070103FF wrapper with no security defaults."""
    return _build_init_amount_frame(
        addr12,
        amount_rupees,
        operator=operator,
        mac1=mac1,
        purchase_count=purchase_count,
        mac2=mac2,
        checksum_mode=checksum_mode,
        include_preamble=True,
    )


def build_power_sale_frame(
    addr12: str,
    amount_rupees,
    order_number: str,
) -> bytes:
    """Compatibility wrapper for the manufacturer-exact recharge frame."""
    return build_charge_frame(addr12, "recharge", order_number, amount_rupees)

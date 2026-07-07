from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class PaymentListRow:
    source: str
    source_type: str
    source_id: int
    lease: object
    date: date
    amount: Decimal
    method: str
    lease_balance: Decimal
    security_balance: Decimal
    view_url: str
    edit_url: Optional[str]
    description: str
    delete_url: Optional[str] = None
    wa_url: Optional[str] = None
    payment_detail_id: Optional[int] = None
    is_split: bool = False
    lease_amount: Decimal = Decimal("0.00")
    security_amount: Decimal = Decimal("0.00")

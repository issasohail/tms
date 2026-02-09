from dataclasses import dataclass
from decimal import Decimal
from datetime import date
from typing import Optional
print("USING ledger.py:", __file__)


@dataclass
class CashLedgerRow:
    source: str           # PAYMENT / SECURITY
    source_type: str      # PAYMENT / REFUND / DAMAGE / ADJUST / REQUIRED etc
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

    # ---- Split/Allocation (only meaningful for PAYMENT rows) ----
    allocation_id: Optional[int] = None
    is_split: bool = False
    lease_amount: Decimal = Decimal("0.00")
    security_amount: Decimal = Decimal("0.00")

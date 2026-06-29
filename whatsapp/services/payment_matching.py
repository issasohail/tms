import re
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .tenant_context import build_lease_context, find_active_leases_for_phone


AMOUNT_RE = re.compile(r"(?:rs\.?|pkr|amount|paid|total)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)", re.I)
REF_RE = re.compile(r"(?:ref|reference|tid|transaction|trx|rrn|id)[\s#:.-]*([A-Z0-9-]{5,})", re.I)


def extract_payment_text_fields(text):
    amount = None
    reference = ""
    for match in AMOUNT_RE.finditer(text or ""):
        try:
            amount = Decimal(match.group(1).replace(",", ""))
            break
        except (InvalidOperation, AttributeError):
            continue
    ref_match = REF_RE.search(text or "")
    if ref_match:
        reference = ref_match.group(1).strip()
    return {
        "amount": amount,
        "date": timezone.localdate(),
        "reference": reference,
        "raw_text": text or "",
    }


def match_payment_to_active_lease(phone_number, ocr_data=None):
    ocr_data = ocr_data or {}
    matches = list(find_active_leases_for_phone(phone_number))
    if not matches:
        return {
            "lease": None,
            "confidence": 0,
            "notes": "No active lease matched by WhatsApp, tenant, or family phone.",
        }

    if len(matches) == 1:
        return {
            "lease": matches[0],
            "confidence": _confidence_for_single_match(matches[0], ocr_data),
            "notes": "Matched by active lease phone priority.",
        }

    amount = ocr_data.get("amount")
    scored = []
    for lease in matches:
        score = 65
        context = build_lease_context(lease)
        if amount and abs(context.balance - amount) <= Decimal("10.00"):
            score += 15
        scored.append((score, lease))
    scored.sort(key=lambda row: row[0], reverse=True)
    best_score, best_lease = scored[0]
    if len(scored) > 1 and scored[1][0] == best_score:
        return {
            "lease": None,
            "confidence": 40,
            "notes": "Multiple active leases matched; tenant confirmation is required.",
            "matches": matches,
        }
    return {
        "lease": best_lease,
        "confidence": min(best_score, 90),
        "notes": "Matched among multiple active leases using phone and amount evidence.",
        "matches": matches,
    }


def _confidence_for_single_match(lease, ocr_data):
    score = 75
    amount = ocr_data.get("amount")
    if amount:
        context = build_lease_context(lease)
        if context.balance and abs(context.balance - amount) <= Decimal("10.00"):
            score += 10
        else:
            score -= 5
    if ocr_data.get("reference"):
        score += 5
    return max(0, min(score, 95))

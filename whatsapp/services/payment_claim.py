"""WhatsApp payment-claim handling (Phase 3).

When a tenant replies to a billing message saying they've already paid, the
system should recognize the claim, check the latest recorded payment and
current balance, ask for a receipt, and advise contacting the landlord for
anything the automated reply can't resolve -- regardless of whether the
tenant's lease is currently active. Prior to this module, tenant identity
resolution for the interactive menu (identity.tenant_matches in
whatsapp.services.identity.sender_resolver) only ever contains tenants who
have an *active* lease, so a tenant whose lease had ended was completely
invisible to the system and got treated as an anonymous guest. This module
resolves tenant + lease independently of that active-lease restriction, for
the narrow purpose of answering a payment claim -- it does not grant that
tenant general "tenant mode" menu access.
"""
import re
from datetime import timedelta

from django.utils import timezone

from leases.models import Lease
from payments.models import Payment
from tenants.models import Tenant
from whatsapp.services.identity.phone_normalizer import normalize_phone_number, phone_matches, searchable_suffix
from whatsapp.services.identity.sender_resolver import TENANT_IDENTITY_PHONE_FIELDS
from whatsapp.services.tenant_context import build_lease_context


AWAITING_PAYMENT_RECEIPT_STATE = "tenant_waiting_payment_receipt"
AWAITING_PAYMENT_RECEIPT_TTL_MINUTES = 60 * 24  # 24 hours


PAYMENT_CLAIM_PATTERNS = [
    r"\bi\s*(have\s*|already\s*)?paid\b",
    r"\balready\s*paid\b",
    r"\bpayment\s*(is\s*)?(already\s*)?done\b",
    r"\bbill\s*paid\b",
    r"\brent\s*paid\b",
    r"\belectricity\s*paid\b",
    r"\bi\s*(have\s*)?sent\s*(the\s*)?(money|payment|amount)\b",
    r"\bi\s*(have\s*)?transferred\s*(the\s*)?amount\b",
    r"\bmain\s*ne\s*.*(paid|payment|ada|bhej)\b",
    r"\bmene\s*.*(paid|payment|ada|bhej)\b",
]
PAYMENT_CLAIM_URDU_SUBSTRINGS = [
    "میں نے ادائیگی کر دی",
    "میں نے پیسے دے دیے",
    "ادائیگی ہو گئی",
    "ادائیگی کر دی ہے",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in PAYMENT_CLAIM_PATTERNS]


def is_payment_claim(text):
    """Best-effort detection of a free-text payment claim.

    Intentionally does not rely on one exact sentence -- covers common
    English and Roman Urdu phrasing plus a few literal Urdu-script phrases.
    False positives are acceptable here (worst case: an unrelated message
    gets a "here's your balance, send a receipt if you already paid"
    reply, which is harmless); false negatives just fall through to normal
    handling.
    """
    value = (text or "").strip()
    if not value:
        return False
    if any(phrase in value for phrase in PAYMENT_CLAIM_URDU_SUBSTRINGS):
        return True
    lowered = value.lower()
    return any(pattern.search(lowered) for pattern in _COMPILED_PATTERNS)


def resolve_tenant_and_last_lease(phone_number):
    """Find a tenant by phone number regardless of lease status, and their
    most relevant lease (active first, else the most recent ended/inactive
    one).

    Returns (tenant, lease, lease_status) where lease_status is one of
    "active", "ended", or "" (tenant matched but has no lease at all).
    If no tenant matches at all, returns (None, None, "").

    Deliberately separate from whatsapp.services.identity.sender_resolver's
    tenant_matches / eligible_tenant_ids filtering: that resolver decides
    who gets interactive "tenant mode" menu access (active lease only, by
    design) and must not be loosened here. This function only answers "who
    is this person and what was their last tenancy," for payment-claim and
    display purposes.
    """
    normalized = normalize_phone_number(phone_number)
    suffix = searchable_suffix(normalized)
    if not suffix:
        return None, None, ""

    from django.db.models import Q
    query = Q()
    for field_name in TENANT_IDENTITY_PHONE_FIELDS:
        query |= Q(**{f"{field_name}__icontains": suffix})
    candidates = Tenant.objects.filter(query, is_active=True).order_by("id")
    tenants = [
        item for item in candidates
        if any(phone_matches(normalized, getattr(item, field_name, "")) for field_name in TENANT_IDENTITY_PHONE_FIELDS)
    ]
    if not tenants:
        return None, None, ""
    # Multiple tenant records sharing one phone number: pick deterministically
    # by most recently updated, matching the ordering already used elsewhere
    # (role_mode._find_tenant) for the same ambiguous-phone situation.
    tenant = sorted(tenants, key=lambda t: (t.updated_at, t.pk), reverse=True)[0] if hasattr(tenants[0], "updated_at") else tenants[0]

    today = timezone.localdate()
    tenant_leases = Lease.objects.filter(
        Q(tenant=tenant) | Q(family_members__family_member=tenant) | Q(legacy_family_members__tenant=tenant)
    ).select_related("tenant", "unit__property").distinct()

    active = tenant_leases.filter(status="active", start_date__lte=today, end_date__gte=today).order_by("-end_date").first()
    if active:
        return tenant, active, "active"

    ended = tenant_leases.order_by("-end_date", "-start_date", "-updated_at", "-id").first()
    if ended:
        return tenant, ended, "ended"

    return tenant, None, ""


def build_payment_claim_reply(tenant, lease):
    """Build the reply text for a payment-claim message, per the three
    documented response branches."""
    if tenant is None:
        return (
            "Please send a clear photo or screenshot of the payment receipt "
            "and include your property, unit, or invoice number. Management "
            "will verify the payment. For further clarification, please "
            "contact your landlord or property management."
        )

    tenant_name = tenant.get_full_name() if hasattr(tenant, "get_full_name") else f"{tenant.first_name} {tenant.last_name}".strip()

    if lease is None:
        return (
            f"Thank you, {tenant_name}.\n\n"
            "We could not find an active or past lease on record for this "
            "number. Please send a clear photo or screenshot of the payment "
            "receipt and include your property, unit, or invoice number so "
            "management can verify it. For further clarification, please "
            "contact your landlord or property management."
        )

    context = build_lease_context(lease)
    latest_payment = context.recent_payments[0] if context.recent_payments else None
    balance = context.balance

    if latest_payment:
        return (
            f"Thank you, {tenant_name}.\n\n"
            f"Our records show your latest payment of PKR {latest_payment.amount} "
            f"dated {latest_payment.payment_date}.\n"
            f"Your current recorded outstanding balance is PKR {balance}.\n\n"
            "Please send a clear photo or screenshot of the payment receipt so "
            "management can verify it. For any discrepancy or further "
            "clarification, please contact your landlord or property "
            "management."
        )

    return (
        f"Thank you, {tenant_name}.\n\n"
        "We have not yet found a recent posted payment in our records. "
        "Please send a clear photo or screenshot of your payment receipt so "
        "management can verify it.\n\n"
        f"Your current recorded outstanding balance is PKR {balance}. For "
        "further clarification, please contact your landlord or property "
        "management."
    )


def set_awaiting_payment_receipt(conversation, tenant, lease):
    conversation.pending_state = AWAITING_PAYMENT_RECEIPT_STATE
    conversation.context["awaiting_payment_receipt_expires_at"] = (
        timezone.now() + timedelta(minutes=AWAITING_PAYMENT_RECEIPT_TTL_MINUTES)
    ).isoformat()
    conversation.context["awaiting_payment_receipt_tenant_id"] = tenant.pk if tenant else None
    conversation.context["awaiting_payment_receipt_lease_id"] = lease.pk if lease else None
    conversation.save(update_fields=["pending_state", "context", "updated_at"])


def is_awaiting_payment_receipt_active(conversation):
    if conversation.pending_state != AWAITING_PAYMENT_RECEIPT_STATE:
        return False
    expires_raw = (conversation.context or {}).get("awaiting_payment_receipt_expires_at")
    if not expires_raw:
        return True
    try:
        from django.utils.dateparse import parse_datetime
        expires_at = parse_datetime(expires_raw)
    except Exception:
        return True
    if expires_at and timezone.now() > expires_at:
        return False
    return True


def clear_awaiting_payment_receipt(conversation):
    conversation.pending_state = ""
    for key in (
        "awaiting_payment_receipt_expires_at",
        "awaiting_payment_receipt_tenant_id",
        "awaiting_payment_receipt_lease_id",
    ):
        conversation.context.pop(key, None)
    conversation.save(update_fields=["pending_state", "context", "updated_at"])

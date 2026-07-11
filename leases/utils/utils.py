from dateutil.relativedelta import relativedelta
from django.contrib.humanize.templatetags.humanize import intcomma
from django.utils import timezone
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib import colors
from reportlab.lib.units import inch
from leases.models import Lease
from datetime import timedelta
from dateutil.relativedelta import relativedelta


def number_to_words(n):
    """Convert numbers to words (e.g., 1000 -> 'One Thousand')"""
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
            "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty",
            "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    if n == 0:
        return "Zero"

    def convert_under_1000(num):
        if num == 0:
            return ""
        elif num < 20:
            return ones[num]
        elif num < 100:
            return tens[num // 10] + (" " + ones[num % 10] if num % 10 > 0 else "")
        else:
            return ones[num // 100] + " Hundred" + (" and " + convert_under_1000(num % 100) if num % 100 > 0 else "")

    parts = []
    if n >= 1000000:
        parts.append(convert_under_1000(n // 1000000) + " Million")
        n %= 1000000
    if n >= 1000:
        parts.append(convert_under_1000(n // 1000) + " Thousand")
        n %= 1000
    if n > 0:
        parts.append(convert_under_1000(n))

    return " ".join(parts)


def inventory_wardrobes(lease):
    if not lease:
        return 0
    lease_value = getattr(lease, "inventory_wardrobes", None)
    if lease_value is not None:
        return lease_value
    unit = getattr(lease, "unit", None)
    return getattr(unit, "wardrobes", 0) if unit else 0




def _authorized_occupant_rows(lease):
    manager = getattr(lease, "family_members", None)
    if manager is None:
        return []
    return list(manager.select_related("family_member", "relationship_type").filter(lives_with_tenant=True))

def authorized_occupants_names(lease):
    return ", ".join(link.family_member.get_full_name() for link in _authorized_occupant_rows(lease))

def authorized_occupants_count(lease):
    return len(_authorized_occupant_rows(lease))

def authorized_occupants_table(lease):
    """Compact three-person-per-row occupant table, including the primary tenant."""
    from django.utils.html import escape
    occupants = [{
        "name": lease.tenant.get_full_name(),
        "cnic": lease.tenant.cnic or "",
        "relationship": "Primary Tenant",
    }]
    for link in _authorized_occupant_rows(lease):
        occupants.append({
            "name": link.family_member.get_full_name(),
            "cnic": link.family_member.cnic or "",
            "relationship": link.relation or getattr(link.relationship_type, "name", "") or "Family Member",
        })
    cells = []
    for index, item in enumerate(occupants, 1):
        cells.append(
            '<td class="occupant-card"><b>{idx}. {name}</b><br>'
            '<span>CNIC: {cnic}</span><br><span>{rel}</span></td>'.format(
                idx=index, name=escape(item["name"]), cnic=escape(item["cnic"]), rel=escape(item["relationship"])
            )
        )
    while len(cells) % 3:
        cells.append('<td class="occupant-card">&nbsp;<br>&nbsp;<br>&nbsp;</td>')
    rows = ['<tr>' + ''.join(cells[i:i + 3]) + '</tr>' for i in range(0, len(cells), 3)]
    return '<table class="authorized-occupants-table compact"><tbody>' + ''.join(rows) + '</tbody></table>'

# Define the placeholder registry
PLACEHOLDER_REGISTRY = {
    "authorized_occupants_table": authorized_occupants_table,
    "authorized_occupants_names": authorized_occupants_names,
    "authorized_occupants_count": authorized_occupants_count,
    "AUTHORIZED_OCCUPANTS_TABLE": authorized_occupants_table,
    "AUTHORIZED_OCCUPANTS_NAMES": authorized_occupants_names,
    "AUTHORIZED_OCCUPANTS_COUNT": authorized_occupants_count,

    # Rent and Maintenance
    "MONTHLY_RENT": lambda lease: lease.monthly_rent,
    "SOCIETY_MAINTENANCE": lambda lease: lease.society_maintenance or 0,
    "TOTAL_MONTHLY": lambda lease: lease.monthly_rent + (lease.society_maintenance or 0),
    "MONTHLY_RENT_IN_WORDS": lambda lease: number_to_words(int(lease.monthly_rent)),
    "TOTAL_MONTHLY_IN_WORDS": lambda lease: number_to_words(int(lease.monthly_rent + (lease.society_maintenance or 0))),
    "LEASE_DURATION_MONTHS": lambda lease: (lambda rd: rd.years * 12 + rd.months)(relativedelta((lease.end_date + timedelta(days=1)), lease.start_date)),
    "DUE_DATE": lambda lease: lease.due_date,

    # Security Deposit
    "SECURITY_DEPOSIT": lambda lease: lease.security_deposit,
    "SECURITY_DEPOSIT_IN_WORDS": lambda lease: number_to_words(int(lease.security_deposit)),
    "SECURITY_PAID": lambda lease: lease.security_deposit_paid or 0,
    "SECURITY_BALANCE": lambda lease: lease.security_deposit - (lease.security_deposit_paid or 0),
    "SECURITY_BALANCE_DUE_DATE": lambda lease: lease.security_deposit_due_date.strftime('%b %d, %Y') if lease.security_deposit_due_date else "",

    # Security Deposit Installments
    "SECURITY_INSTALLMENT_1_AMOUNT": lambda lease: lease.security_installment_1_amount or "",
    "SECURITY_INSTALLMENT_1_DATE": lambda lease: lease.security_installment_1_date.strftime('%b %d, %Y') if lease.security_installment_1_date else "",
    "SECURITY_INSTALLMENT_2_AMOUNT": lambda lease: lease.security_installment_2_amount or "",
    "SECURITY_INSTALLMENT_2_DATE": lambda lease: lease.security_installment_2_date.strftime('%b %d, %Y') if lease.security_installment_2_date else "",



    # Late Fee and Due Date
    "LEASE_DUE_DATE": lambda lease: lease.due_date.strftime('%b %d, %Y') if lease.due_date else "",
    "LATE_FEE": lambda lease: lease.late_fee or 0,

    # Clause #6 (Minimum Occupancy)
    "MIN_OCCUPANCY_PERIOD": lambda lease: lease.min_lease_occupancy_months or 0,
    "EARLY_TERMINATION_PENALTY": lambda lease: lease.early_termination_penalty or 0,
    "RENT_INCREASE_PERCENT": lambda lease: lease.rent_increase_percent,

    # Key Info
    "KEYS_ISSUED": lambda lease: lease.keys_issued or 0,
    "KEY_REPLACEMENT_COST": lambda lease: lease.key_replacement_cost or 0,

    # Meter Readings
    "ELECTRIC_UNIT_RATE": lambda lease: lease.electric_unit_rate or 0,
    "ELECTRICITY_METER_READING": lambda lease: lease.electricity_meter_reading or "N/A",
    "GAS_METER_READING": lambda lease: lease.water_meter_reading or "N/A",
    "ELECTRIC_METER_NUM": lambda lease: lease.unit.electric_meter_num if lease.unit else "N/A",
    "GAS_METER_NUM": lambda lease: lease.unit.gas_meter_num if lease.unit else "N/A",

    # Unit Inventory
    "INVENTORY_CEILING_FANS": lambda lease: lease.unit.ceiling_fan if lease.unit else 0,
    "INVENTORY_LIGHTS": lambda lease: lease.unit.ceiling_lights if lease.unit else 0,
    "INVENTORY_EXHAUST_FANS": lambda lease: lease.unit.exhaust_fan if lease.unit else 0,
    "INVENTORY_STOVE": lambda lease: lease.unit.stove if lease.unit else 0,
    "INVENTORY_WARDROBE": inventory_wardrobes,
    "WARDROBE": inventory_wardrobes,

    "PAINT_CONDIDTION": lambda lease: lease.unit.paint_condition if lease.unit else 0,

    # Dates
    "START_DATE": lambda lease: lease.start_date.strftime('%b %d, %Y') if lease.start_date else "",
    "END_DATE": lambda lease: lease.end_date.strftime('%b %d, %Y') if lease.end_date else "",
    "METER_READING_DATE": lambda lease: timezone.now().strftime('%b %d, %Y'),
}

# Rest of your utility functions


def generate_lease_agreement(lease):
    # ... (your existing generate_lease_agreement function) ...
    pass


def resolve_placeholders(lease, clause_text):
    # ... (your existing resolve_placeholders function) ...
    pass


def generate_agreement_html(lease):
    # ... (your existing generate_agreement_html function) ...
    pass


def _lease_bank_account(lease):
    if not lease:
        return ""
    unit = getattr(lease, "unit", None)
    property_obj = getattr(unit, "property", None)
    unit_bank = (getattr(unit, "bank_account_details", None) or "").strip()
    use_property = getattr(unit, "use_property_bank_account", True)
    property_bank = (getattr(property_obj, "bank_account_details", None) or "").strip()
    if unit_bank and not use_property:
        return unit_bank
    return property_bank or unit_bank


def _db_placeholders_for_lease(AgreementPlaceholder, lease=None):
    cache_attr = "_active_db_agreement_placeholders"

    # Read only a cache value that was explicitly stored on the instance.
    # Using hasattr()/getattr() is unsafe with Mock objects and dynamic proxy
    # objects because they may fabricate arbitrary attributes.
    lease_dict = getattr(lease, "__dict__", {}) if lease is not None else {}
    cached = lease_dict.get(cache_attr) if isinstance(lease_dict, dict) else None
    if isinstance(cached, (list, tuple)):
        return cached

    placeholders = list(
        AgreementPlaceholder.objects.filter(
            is_active=True,
            source_type__in=[
                AgreementPlaceholder.SOURCE_CUSTOM,
                AgreementPlaceholder.SOURCE_MANUAL,
            ],
        )
    )
    if lease is not None:
        setattr(lease, cache_attr, placeholders)
    return placeholders


def replace_db_placeholders(text, lease=None):
    """
    Replace UI-managed custom/manual placeholders after system placeholders.
    Unknown placeholders are intentionally left unchanged.
    """
    try:
        from leases.models import AgreementPlaceholder
    except Exception:
        return text

    placeholders = _db_placeholders_for_lease(AgreementPlaceholder, lease)
    for placeholder in placeholders:
        token = f"[{placeholder.key}]"
        if token in text:
            if placeholder.key == "BANK_ACCOUNT":
                replacement = _lease_bank_account(lease) or placeholder.default_value or ""
            else:
                replacement = placeholder.default_value or ""
            text = text.replace(token, replacement)
    return text


def do_replace_placeholders(text, lease):
    """Replace placeholders in clause text with actual values.
       Returns HTML (e.g., <strong>...</strong>) for preview/PDF rendering.
    """
    money_terms = [
        'MONTHLY_RENT', 'LATE_FEE', 'DEPOSIT', 'MAINTENANCE', 'TOTAL',
        'KEY_REPLACEMENT_COST', 'EARLY_TERMINATION_PENALTY'
    ]

    for placeholder, func in PLACEHOLDER_REGISTRY.items():
        tokens = (f"[{placeholder}]", f"{{{{{placeholder}}}}}")
        if any(token in text for token in tokens):
            try:
                replacement = func(lease)

                # money placeholders -> bold number only (NO "Rs." and NO "/-")
                if any(term in placeholder for term in money_terms):
                    try:
                        replacement = f"<strong>{intcomma(int(replacement))}</strong>"
                    except (TypeError, ValueError):
                        replacement = f"<strong>{replacement}</strong>"

                for search_str in tokens:
                    text = text.replace(search_str, str(replacement))
            except Exception as e:
                print(f"Error replacing {placeholder}: {e}")

    return replace_db_placeholders(text, lease)

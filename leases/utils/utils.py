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


# Define the placeholder registry
PLACEHOLDER_REGISTRY = {
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
    "INVENTORY_WARDROBE": lambda lease: lease.unit.wardrobe if lease.unit else 0,
    "WARDROBE": lambda lease: lease.unit.wardrobe if lease.unit else 0,

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


def do_replace_placeholders(text, lease):
    """Replace placeholders in clause text with actual values.
       Returns HTML (e.g., <strong>...</strong>) for preview/PDF rendering.
    """
    money_terms = [
        'MONTHLY_RENT', 'LATE_FEE', 'DEPOSIT', 'MAINTENANCE', 'TOTAL',
        'KEY_REPLACEMENT_COST', 'EARLY_TERMINATION_PENALTY'
    ]

    for placeholder, func in PLACEHOLDER_REGISTRY.items():
        search_str = f"[{placeholder}]"
        if search_str in text:
            try:
                replacement = func(lease)

                # money placeholders -> bold number only (NO "Rs." and NO "/-")
                if any(term in placeholder for term in money_terms):
                    try:
                        replacement = f"<strong>{intcomma(int(replacement))}</strong>"
                    except (TypeError, ValueError):
                        replacement = f"<strong>{replacement}</strong>"

                text = text.replace(search_str, str(replacement))
            except Exception as e:
                print(f"Error replacing {placeholder}: {e}")

    return text

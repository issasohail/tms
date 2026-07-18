from dateutil.relativedelta import relativedelta
from django.contrib.humanize.templatetags.humanize import intcomma
from django.utils import timezone
from django.utils.html import conditional_escape
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib import colors
from reportlab.lib.units import inch
from leases.models import Lease
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from dateutil.relativedelta import relativedelta
from core.utils.identity import format_cnic


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
    """Compact four-person-per-row occupant table, including the tenant."""
    from django.utils.html import escape
    occupants = [{
        "name": lease.tenant.get_full_name(),
        "cnic": format_cnic(lease.tenant.cnic) or "",
        "relationship": "Tenant",
    }]
    for link in _authorized_occupant_rows(lease):
        occupants.append({
            "name": link.family_member.get_full_name(),
            "cnic": format_cnic(link.family_member.cnic) or "",
            "relationship": link.relation or getattr(link.relationship_type, "name", "") or "Family Member",
        })
    cells = []
    for index, item in enumerate(occupants, 1):
        cells.append(
            '<td class="occupant-card"><b>{idx}. {name}</b><br>'
            '<span>CNIC: {cnic}</span><br><span>Relationship: {rel}</span></td>'.format(
                idx=index, name=escape(item["name"]), cnic=escape(item["cnic"]), rel=escape(item["relationship"])
            )
        )
    while len(cells) % 4:
        cells.append('<td class="occupant-card">&nbsp;<br>&nbsp;<br>&nbsp;</td>')
    rows = ['<tr>' + ''.join(cells[i:i + 4]) + '</tr>' for i in range(0, len(cells), 4)]
    return '<table class="authorized-occupants-table compact"><tbody>' + ''.join(rows) + '</tbody></table>'


def _decimal_value(value):
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def _formatted_amount(value):
    amount = _decimal_value(value)
    if amount == amount.to_integral_value():
        return intcomma(int(amount))
    return f"{amount:,.2f}".rstrip("0").rstrip(".")


def total_monthly_amount(lease):
    return sum(
        (
            _decimal_value(getattr(lease, "monthly_rent", 0)),
            _decimal_value(getattr(lease, "society_maintenance", 0)),
            _decimal_value(getattr(lease, "water_charges", 0)),
            _decimal_value(getattr(lease, "internet_charges", 0)),
        ),
        Decimal("0.00"),
    )


def additional_monthly_charges_clause(lease):
    charges = []
    for label, field_name in (
        ("society/building maintenance", "society_maintenance"),
        ("water", "water_charges"),
        ("internet", "internet_charges"),
    ):
        amount = _decimal_value(getattr(lease, field_name, 0))
        if amount > 0:
            charges.append(
                f"{label} charges of Rs. <strong>{_formatted_amount(amount)}</strong>/-"
            )
    if not charges:
        return ""
    if len(charges) == 1:
        charge_text = charges[0]
    else:
        charge_text = ", ".join(charges[:-1]) + f", and {charges[-1]}"
    return f"In addition to rent, the Tenant shall pay {charge_text}. "


def late_fee_clause(lease):
    from leases.models_late_fee import get_effective_late_fee_settings

    config = get_effective_late_fee_settings(lease)
    if not config.get("enabled"):
        return ""

    grace_days = int(config.get("grace_days") or 0)
    interval_days = int(config.get("reminder_interval_days") or 1)
    max_reminders = int(config.get("max_reminders") or 0)
    if config.get("type") == "percent":
        percent = _decimal_value(config.get("percent"))
        if percent <= 0:
            return ""
        charge = f"{_formatted_amount(percent)}% of the outstanding amount"
    else:
        amount = _decimal_value(config.get("amount"))
        if amount <= 0:
            return ""
        charge = f"Rs. <strong>{_formatted_amount(amount)}</strong>/-"

    grace_text = (
        f"within {grace_days} calendar day{'s' if grace_days != 1 else ''} after the due date"
        if grace_days
        else "by the due date"
    )
    wording = (
        f"If payment is not received {grace_text}, a late fee of {charge} will be charged."
    )
    if max_reminders != 1:
        repeat_limit = (
            f", up to a maximum of {max_reminders} charges"
            if max_reminders
            else ""
        )
        wording += (
            f" While payment remains outstanding, the same late fee may be charged again "
            f"every {interval_days} calendar day{'s' if interval_days != 1 else ''}{repeat_limit}."
        )
    return wording + " "


def security_installment_clause(lease):
    installments = []
    for label, amount_field, date_field in (
        ("First", "security_installment_1_amount", "security_installment_1_date"),
        ("Second", "security_installment_2_amount", "security_installment_2_date"),
    ):
        amount = _decimal_value(getattr(lease, amount_field, 0))
        due_date = getattr(lease, date_field, None)
        if amount <= 0 and not due_date:
            continue
        if amount > 0 and due_date:
            detail = (
                f"{label.lower()} installment of Rs. <strong>{_formatted_amount(amount)}</strong>/- "
                f"is due on {due_date:%b %d, %Y}"
            )
        elif amount > 0:
            detail = f"{label.lower()} installment is Rs. <strong>{_formatted_amount(amount)}</strong>/-"
        else:
            detail = f"{label.lower()} installment is due on {due_date:%b %d, %Y}"
        installments.append(detail)
    if not installments:
        return ""
    schedule = "; ".join(installments)
    return (
        f"If the Security Deposit is paid in installments, the agreed schedule is: {schedule}. "
        "If the Tenant fails to pay any installment within seven (7) days of its due date, "
        "the Owner may terminate this Agreement by written notice, and the unpaid amount "
        "will remain recoverable. "
    )


def smart_meter_payment_clause(lease):
    unit = getattr(lease, "unit", None)
    if not unit or not getattr(unit, "is_smart_meter", False):
        return ""
    return (
        "For premises equipped with a smart meter, the Tenant acknowledges that electricity "
        "service may be disconnected remotely for non-payment and restored after all "
        "outstanding amounts are paid and processed."
    )


BLANK_METER_VALUE = "________________"


def _meaningful_meter_number(value):
    number = str(value or "").strip()
    if not number or (number.isdigit() and set(number) == {"0"}):
        return ""
    return number


def _electricity_meter_data(lease):
    cache_key = "_agreement_electricity_meter_data"
    lease_dict = getattr(lease, "__dict__", {})
    if cache_key in lease_dict:
        return lease_dict[cache_key]

    unit = getattr(lease, "unit", None)
    meter = None
    if unit is not None:
        meter_manager = getattr(unit, "current_meters", None)
        if meter_manager is not None:
            meter = (
                meter_manager.filter(is_active=True, meter_type="electric")
                .order_by("meter_role", "id")
                .first()
            )

    meter_number = _meaningful_meter_number(getattr(meter, "meter_number", ""))
    if not meter_number and unit is not None:
        meter_number = _meaningful_meter_number(
            getattr(unit, "electric_meter_num", "")
        )

    reading = None
    reading_timestamp = None
    if meter is not None:
        live_reading = getattr(meter, "latest_live", None)
        if live_reading is not None and live_reading.total_energy is not None:
            reading = f"{live_reading.total_energy:f} kWh"
            reading_timestamp = live_reading.ts
        else:
            historical = (
                meter.readings.filter(total_energy__isnull=False)
                .order_by("-ts")
                .first()
            )
            if historical is not None:
                reading = f"{historical.total_energy:f} kWh"
                reading_timestamp = historical.ts

    if reading is None:
        reading = getattr(lease, "electricity_meter_reading", None)

    data = {
        "meter_number": meter_number or BLANK_METER_VALUE,
        "reading": reading or BLANK_METER_VALUE,
        "reading_timestamp": reading_timestamp,
    }
    setattr(lease, cache_key, data)
    return data


def electricity_meter_number(lease):
    return _electricity_meter_data(lease)["meter_number"]


def electricity_meter_reading(lease):
    return _electricity_meter_data(lease)["reading"]


def meter_reading_date(lease):
    meter_data = _electricity_meter_data(lease)
    if meter_data["reading"] == BLANK_METER_VALUE:
        return BLANK_METER_VALUE
    reading_timestamp = meter_data["reading_timestamp"]
    if reading_timestamp is not None:
        if timezone.is_aware(reading_timestamp):
            reading_timestamp = timezone.localtime(reading_timestamp)
        return reading_timestamp.strftime("%b %d, %Y")
    return timezone.localdate().strftime("%b %d, %Y")


def smart_meter_electricity_terms(lease):
    unit = getattr(lease, "unit", None)
    if not unit or not getattr(unit, "is_smart_meter", False):
        return ""

    meter = None
    meter_manager = getattr(unit, "current_meters", None)
    if meter_manager is not None:
        meter = meter_manager.filter(is_active=True).order_by("id").first()
    unit_rate = (
        _decimal_value(getattr(lease, "electric_unit_rate", 0))
        or _decimal_value(getattr(meter, "unit_rate", 0))
        or Decimal("50.00")
    )
    fixed_charge = (
        _decimal_value(getattr(meter, "service_charges", 0))
        or Decimal("250.00")
    )
    return (
        "The electricity bill will be calculated at Rs. "
        f"<strong>{_formatted_amount(unit_rate)}</strong>/- per unit with a fixed charge "
        f"of Rs. <strong>{_formatted_amount(fixed_charge)}</strong>/- per month, billed at "
        "the end of each month and payable with the rent to the Owner. The electricity is "
        "prepaid-programmed, so delay in rental payment may cause electricity service disruption."
    )


def inventory_list(lease):
    from leases.services.inventory_parking import inventory_list_html
    return inventory_list_html(lease)


def parking_clause(lease):
    from leases.services.inventory_parking import parking_clause_html
    return parking_clause_html(lease)


def parking_space(lease):
    from leases.services.inventory_parking import parking_space_label
    return parking_space_label(lease)


def parking_monthly_rate(lease):
    from leases.services.inventory_parking import effective_parking_monthly_rate
    return effective_parking_monthly_rate(lease)


def unauthorized_parking_penalty(lease):
    from leases.services.inventory_parking import effective_unauthorized_parking_penalty
    return effective_unauthorized_parking_penalty(lease)


def parking_assignment_terms(lease):
    from leases.services.inventory_parking import parking_assignment_terms_html
    return parking_assignment_terms_html(lease)


def parking_enabled(lease):
    from leases.services.inventory_parking import effective_parking_policy, policy_value
    return "Yes" if policy_value(effective_parking_policy(lease=lease), "enabled") else "No"


def water_abuse_penalty(lease):
    from core.models import GlobalSettings
    return GlobalSettings.get_solo().water_abuse_penalty_amount

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
    "WATER_CHARGES": lambda lease: lease.water_charges or 0,
    "INTERNET_CHARGES": lambda lease: lease.internet_charges or 0,
    "ADDITIONAL_MONTHLY_CHARGES_CLAUSE": additional_monthly_charges_clause,
    "TOTAL_MONTHLY": total_monthly_amount,
    "MONTHLY_RENT_IN_WORDS": lambda lease: number_to_words(int(lease.monthly_rent)),
    "TOTAL_MONTHLY_IN_WORDS": lambda lease: number_to_words(int(total_monthly_amount(lease))),
    "LEASE_DURATION_MONTHS": lambda lease: (lambda rd: rd.years * 12 + rd.months)(relativedelta((lease.end_date + timedelta(days=1)), lease.start_date)),
    "DUE_DATE": lambda lease: str(lease.due_date or "the stated due date").rstrip(". "),
    "PRORATION_INTERVAL_DAYS": lambda lease: lease.effective_proration_interval_days,
    "PRORATION_INTERVAL_LABEL": lambda lease: lease.effective_proration_interval_label,

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
    "SECURITY_INSTALLMENT_CLAUSE": security_installment_clause,



    # Late Fee and Due Date
    "LEASE_DUE_DATE": lambda lease: lease.due_date.strftime('%b %d, %Y') if lease.due_date else "",
    "LATE_FEE": lambda lease: lease.late_fee or 0,
    "LATE_FEE_CLAUSE": late_fee_clause,
    "SMART_METER_PAYMENT_CLAUSE": smart_meter_payment_clause,

    # Clause #6 (Minimum Occupancy)
    "MIN_OCCUPANCY_PERIOD": lambda lease: lease.min_lease_occupancy_months or 0,
    "EARLY_TERMINATION_PENALTY": lambda lease: lease.early_termination_penalty or 0,
    "RENT_INCREASE_PERCENT": lambda lease: lease.rent_increase_percent,

    # Key Info
    "KEYS_ISSUED": lambda lease: lease.keys_issued or 0,
    "KEY_REPLACEMENT_COST": lambda lease: lease.key_replacement_cost or 0,

    # Meter Readings
    "ELECTRIC_UNIT_RATE": lambda lease: lease.electric_unit_rate or 0,
    "ELECTRICITY_METER_NUMBER": electricity_meter_number,
    "ELECTRICITY_METER_READING": electricity_meter_reading,
    "GAS_METER_READING": lambda lease: lease.water_meter_reading or "N/A",
    "ELECTRIC_METER_NUM": lambda lease: lease.unit.electric_meter_num if lease.unit else "N/A",
    "GAS_METER_NUM": lambda lease: lease.unit.gas_meter_num if lease.unit else "N/A",
    "SMART_METER_ELECTRICITY_TERMS": smart_meter_electricity_terms,

    # Unit Inventory
    "INVENTORY_CEILING_FANS": lambda lease: lease.unit.ceiling_fan if lease.unit else 0,
    "INVENTORY_LIGHTS": lambda lease: lease.unit.ceiling_lights if lease.unit else 0,
    "INVENTORY_EXHAUST_FANS": lambda lease: lease.unit.exhaust_fan if lease.unit else 0,
    "INVENTORY_STOVE": lambda lease: lease.unit.stove if lease.unit else 0,
    "INVENTORY_WARDROBE": inventory_wardrobes,
    "WARDROBE": inventory_wardrobes,
    "INVENTORY_LIST": inventory_list,
    "PARKING_CLAUSE": parking_clause,
    "PARKING_SPACE": parking_space,
    "PARKING_MONTHLY_RATE": parking_monthly_rate,
    "UNAUTHORIZED_PARKING_PENALTY": unauthorized_parking_penalty,
    "PARKING_ASSIGNMENT_TERMS": parking_assignment_terms,
    "PARKING_ENABLED": parking_enabled,
    "WATER_ABUSE_PENALTY": water_abuse_penalty,

    "PAINT_CONDIDTION": lambda lease: lease.unit.paint_condition if lease.unit else 0,

    # Dates
    "START_DATE": lambda lease: lease.start_date.strftime('%b %d, %Y') if lease.start_date else "",
    "END_DATE": lambda lease: lease.end_date.strftime('%b %d, %Y') if lease.end_date else "",
    "METER_READING_DATE": meter_reading_date,
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
    structured_resolver = getattr(property_obj, "welcome_bank_account_details", None)
    if callable(structured_resolver):
        structured_bank = structured_resolver()
        if structured_bank:
            return structured_bank
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
            text = text.replace(
                token, f"<strong>{conditional_escape(replacement)}</strong>"
            )
    return text


def do_replace_placeholders(text, lease):
    """Replace placeholders in clause text with actual values.
       Returns HTML (e.g., <strong>...</strong>) for preview/PDF rendering.
    """
    money_terms = [
        'MONTHLY_RENT', 'LATE_FEE', 'DEPOSIT', 'MAINTENANCE', 'TOTAL',
        'KEY_REPLACEMENT_COST', 'EARLY_TERMINATION_PENALTY', 'PENALTY',
        'PARKING_MONTHLY_RATE'
    ]

    for placeholder, func in PLACEHOLDER_REGISTRY.items():
        tokens = (f"[{placeholder}]", f"{{{{{placeholder}}}}}")
        if any(token in text for token in tokens):
            try:
                replacement = func(lease)

                fragment_placeholder = placeholder.upper().endswith(
                    ("_CLAUSE", "_TABLE", "_LIST", "_TERMS")
                )
                if not fragment_placeholder and any(term in placeholder for term in money_terms):
                    try:
                        replacement = f"<strong>{intcomma(int(replacement))}</strong>"
                    except (TypeError, ValueError):
                        replacement = f"<strong>{conditional_escape(replacement)}</strong>"
                elif not fragment_placeholder:
                    replacement = f"<strong>{conditional_escape(replacement)}</strong>"

                for search_str in tokens:
                    text = text.replace(search_str, str(replacement))
            except Exception as e:
                print(f"Error replacing {placeholder}: {e}")

    return replace_db_placeholders(text, lease)

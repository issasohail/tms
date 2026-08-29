import re
from decimal import Decimal
from urllib.parse import quote

from django.urls import reverse

from leases.models import AgreementPlaceholder, WhatsAppTemplate
from core.public_urls import build_public_url
from core.utils.identity import format_phone


DEFAULT_TENANT_WELCOME_MESSAGE = """*Welcome, [TENANT_NAME]!*

Your rental agreement for *[PROPERTY_NAME] - Unit [UNIT_NUMBER]* has been generated.

*Lease details*
• Agreement period: [LEASE_START_DATE] to [LEASE_END_DATE]
• Total monthly payment: Rs. [TOTAL_MONTHLY_PAYMENT]
• Security deposit: Rs. [SECURITY_DEPOSIT]
• Rent due: [DUE_DATE]
[SMART_METER_DETAILS]

*Owner payment account*
[BANK_ACCOUNT]

[LATE_FEE_NOTICE]

Failure to make payment on time may affect utility services such as electricity, water, and internet.

*Important WhatsApp contact*
Please save *[BUSINESS_WHATSAPP_NUMBER]*. You will receive invoices from this number. You can also use it to:
• Send payment receipts
• Report maintenance issues
• Ask rental or payment questions

After making a payment, send the payment receipt to this WhatsApp number. If you do not receive a payment receipt or confirmation message within 24 hours, please contact the property owner or manager at [PROPERTY_CONTACT_NUMBER].

*Before moving in*
1. Read the agreement carefully, sign it, and submit the signed copy.
2. Submit a copy of your police verification report.
3. Tell the property owner or manager if anything in the unit is not working.
4. Review and sign the inspection sheet together with the agreement.

Thank you, and welcome to your new home!"""


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


def normalize_whatsapp_phone(phone, country_code="+92"):
    digits = re.sub(r"\D+", "", phone or "")
    country = re.sub(r"\D+", "", country_code or "")
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and country:
        digits = country + digits[1:]
    return digits


def _money(value):
    value = value or Decimal("0")
    try:
        return f"{Decimal(value):,.0f}"
    except Exception:
        return str(value)


def _date(value):
    return value.strftime("%Y-%m-%d") if value else ""


def _contact_phone(value):
    raw = str(value or "").strip()
    digits = re.sub(r"\D+", "", raw)
    if len(digits) == 11 and digits.startswith("0"):
        return f"{digits[:4]}-{digits[4:7]}-{digits[7:]}"
    return format_phone(raw)


def _lease_balance(lease):
    balance = getattr(lease, "get_balance", 0)
    if callable(balance):
        balance = balance()
    return balance or 0


def _total_monthly_payment(lease):
    return sum(
        (
            getattr(lease, "monthly_rent", 0) or 0,
            getattr(lease, "society_maintenance", 0) or 0,
            getattr(lease, "water_charges", 0) or 0,
            getattr(lease, "internet_charges", 0) or 0,
        )
    )


def _reading(value):
    if value in (None, ""):
        return ""
    try:
        return f"{Decimal(value):,.3f}"
    except Exception:
        return str(value)


def _smart_meter_details(lease, unit):
    if not unit or not getattr(unit, "is_smart_meter", False):
        return ""

    meter = None
    meter_manager = getattr(unit, "current_meters", None)
    if meter_manager is not None:
        meter = (
            meter_manager.filter(
                is_active=True,
                meter_type="electric",
                meter_role="billing",
            )
            .order_by("id")
            .first()
        )

    meter_number = (
        getattr(meter, "meter_number", "")
        or getattr(unit, "electric_meter_num", "")
        or "Not recorded"
    )
    live_reading = getattr(getattr(meter, "latest_live", None), "total_energy", None)
    reading = _reading(
        live_reading
        if live_reading is not None
        else getattr(lease, "electricity_meter_reading", None)
    ) or "Not available"
    return f"• Smart electric meter: {meter_number}\n• Current meter reading: {reading} kWh"


def _meter_numbers(unit):
    meters = []
    electric_meter = (getattr(unit, "electric_meter_num", "") or "").strip()
    gas_meter = (getattr(unit, "gas_meter_num", "") or "").strip()
    if electric_meter:
        meters.append(f"Electric: {electric_meter}")
    if gas_meter:
        meters.append(f"Gas: {gas_meter}")
    try:
        smart_meter = getattr(unit, "meter", None)
        if smart_meter and getattr(smart_meter, "meter_number", ""):
            meters.append(f"Smart: {smart_meter.meter_number}")
    except Exception:
        pass
    return ", ".join(meters)


def lease_whatsapp_context(lease, request=None):
    from core.models import GlobalSettings
    from leases.models_late_fee import get_effective_late_fee_settings

    tenant = getattr(lease, "tenant", None)
    unit = getattr(lease, "unit", None)
    property_obj = getattr(unit, "property", None)
    settings_obj = GlobalSettings.get_solo()
    late_fee_settings = get_effective_late_fee_settings(lease)
    due_date = getattr(lease, "due_date", "") or "the stated due date"
    due_date_in_sentence = str(due_date).rstrip(". ")
    late_fee_notice = (
        f"Please pay by {due_date_in_sentence} to avoid late-payment charges."
    )
    if late_fee_settings.get("enabled"):
        interval_days = int(late_fee_settings.get("reminder_interval_days") or 1)
        grace_days = int(late_fee_settings.get("grace_days") or 0)
        if late_fee_settings.get("type") == "percent":
            fee_charge = f"{_money(late_fee_settings.get('percent'))}%"
        else:
            amount = late_fee_settings.get("amount") or getattr(lease, "late_fee", 0)
            fee_charge = f"Rs. {_money(amount)}"
        grace_text = (
            f" after a {grace_days}-day grace period"
            if grace_days
            else " after the due date"
        )
        late_fee_notice = (
            f"Please pay by {due_date_in_sentence}. Late payment may incur a charge of "
            f"{fee_charge} every {interval_days} days{grace_text}."
        )

    context = {
        "TENANT_NAME": tenant.get_full_name() if tenant else "",
        "BUILDING_NAME": getattr(property_obj, "property_name", "") or "",
        "PROPERTY_NAME": getattr(property_obj, "property_name", "") or "",
        "UNIT_NUMBER": getattr(unit, "unit_number", "") or "",
        "LEASE_START_DATE": _date(getattr(lease, "start_date", None)),
        "LEASE_END_DATE": _date(getattr(lease, "end_date", None)),
        # Backward-compatible aliases used by older saved WhatsApp templates.
        "START_DATE": _date(getattr(lease, "start_date", None)),
        "END_DATE": _date(getattr(lease, "end_date", None)),
        "DUE_DATE": due_date,
        "MONTHLY_RENT": _money(getattr(lease, "monthly_rent", 0)),
        "TOTAL_MONTHLY_PAYMENT": _money(_total_monthly_payment(lease)),
        "TOTAL_PAYMENT": _money(_total_monthly_payment(lease)),
        "SECURITY_DEPOSIT": _money(getattr(lease, "security_deposit", 0)),
        "BALANCE_AMOUNT": _money(_lease_balance(lease)),
        "METER_NUMBERS": _meter_numbers(unit),
        "BANK_ACCOUNT": _lease_bank_account(lease) or (
            "Bank account information has not been recorded. Please contact the "
            "property owner or manager before making payment."
        ),
        "SMART_METER_DETAILS": _smart_meter_details(lease, unit),
        "BUSINESS_WHATSAPP_NUMBER": _contact_phone(settings_obj.whatsapp_number),
        "OFFICE_NUMBER": _contact_phone(settings_obj.whatsapp_number),
        "ELECTRIC_METER": getattr(unit, "electric_meter_num", "") or "Not recorded",
        "PROPERTY_CONTACT_NUMBER": _contact_phone(
            getattr(property_obj, "caretaker_phone", "")
            or getattr(property_obj, "owner_phone", "")
        ) or "the contact number provided to you",
        "LATE_FEE_NOTICE": late_fee_notice,
    }

    if request is not None and unit is not None:
        context["UNIT_PHOTO_ADMIN_LINK"] = build_public_url(
            "properties:unit_media_share_link", args=[unit.pk]
        )

    for placeholder in AgreementPlaceholder.objects.filter(is_active=True):
        context.setdefault(placeholder.key, placeholder.default_value or "")
    return context


def unit_whatsapp_context(unit, request=None):
    property_obj = getattr(unit, "property", None)
    context = {
        "BUILDING_NAME": getattr(property_obj, "property_name", "") or "",
        "PROPERTY_NAME": getattr(property_obj, "property_name", "") or "",
        "UNIT_NUMBER": getattr(unit, "unit_number", "") or "",
        "BEDROOMS": getattr(unit, "bedrooms", "") or "",
        "BATHROOMS": getattr(unit, "bathrooms", "") or "",
        "MONTHLY_RENT": _money(getattr(unit, "monthly_rent", 0)),
        "SECURITY_DEPOSIT": getattr(unit, "security_requires", "") or "",
        "SOCIETY_MAINTENANCE": _money(getattr(unit, "society_maintenance", 0)),
        "WATER_CHARGES": _money(getattr(unit, "water_charges", 0)),
        "AGREEMENT_CHARGES": "",
        "AVAILABILITY_DATE": "",
        "CONDITIONS_RULES": getattr(unit, "comments", "") or "",
        "CONTACT_NUMBER": format_phone(getattr(property_obj, "caretaker_phone", "") or getattr(property_obj, "owner_phone", "")),
        "METER_NUMBERS": _meter_numbers(unit),
        "BANK_ACCOUNT": _lease_bank_account(type("LeaseLike", (), {"unit": unit})()),
        "UNIT_PHOTO_LINK": "",
        "UNIT_PHOTO_MESSAGE": "",
    }
    if request is not None and unit is not None:
        from properties.models import PublicPhotoLink
        from properties.services.photo_link_renewal import (
            public_link_share_text,
            public_link_url,
            reusable_public_photo_link,
        )

        link = reusable_public_photo_link(
            PublicPhotoLink.GALLERY_UNIT,
            property_obj=unit.property,
            unit=unit,
            created_by=(
                request.user
                if getattr(request.user, "is_authenticated", False)
                else None
            ),
        )
        context["UNIT_PHOTO_LINK"] = public_link_url(link)
        context["UNIT_PHOTO_MESSAGE"] = public_link_share_text(link)

    for placeholder in AgreementPlaceholder.objects.filter(is_active=True):
        context.setdefault(placeholder.key, placeholder.default_value or "")
    return context


def render_whatsapp_template(template_type, lease, request=None):
    template = WhatsAppTemplate.objects.filter(
        template_type=template_type,
        is_active=True,
    ).first()
    body = template.body if template else ""
    if (
        template_type == WhatsAppTemplate.TEMPLATE_TENANT_WELCOME
        and template
        and not body.strip()
    ):
        body = DEFAULT_TENANT_WELCOME_MESSAGE
    context = lease_whatsapp_context(lease, request=request)
    rendered = body or ""
    for key, value in context.items():
        rendered = rendered.replace(f"[{key}]", str(value or ""))
    return template, rendered


def render_unit_whatsapp_template(template_type, unit, request=None):
    template = WhatsAppTemplate.objects.filter(
        template_type=template_type,
        is_active=True,
    ).first()
    body = template.body if template else ""
    context = unit_whatsapp_context(unit, request=request)
    rendered = body or ""
    if template_type == WhatsAppTemplate.TEMPLATE_VACANCY and rendered.strip():
        photo_message = context.get("UNIT_PHOTO_MESSAGE", "")
        if photo_message and "[UNIT_PHOTO_MESSAGE]" not in rendered:
            if "[UNIT_PHOTO_LINK]" in rendered:
                rendered = rendered.replace("[UNIT_PHOTO_LINK]", photo_message)
            else:
                rendered = f"{rendered.rstrip()}\n\n{photo_message}"
    for key, value in context.items():
        rendered = rendered.replace(f"[{key}]", str(value or ""))
    return template, rendered


def build_whatsapp_url(phone, message, country_code="+92"):
    normalized = normalize_whatsapp_phone(phone, country_code=country_code)
    if not normalized:
        return ""
    return f"https://wa.me/{normalized}?text={quote(message or '')}"

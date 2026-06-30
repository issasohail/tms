import re
from decimal import Decimal
from urllib.parse import quote

from django.urls import reverse

from leases.models import AgreementPlaceholder, WhatsAppTemplate


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


def _lease_balance(lease):
    balance = getattr(lease, "get_balance", 0)
    if callable(balance):
        balance = balance()
    return balance or 0


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
    tenant = getattr(lease, "tenant", None)
    unit = getattr(lease, "unit", None)
    property_obj = getattr(unit, "property", None)
    context = {
        "TENANT_NAME": tenant.get_full_name() if tenant else "",
        "BUILDING_NAME": getattr(property_obj, "property_name", "") or "",
        "PROPERTY_NAME": getattr(property_obj, "property_name", "") or "",
        "UNIT_NUMBER": getattr(unit, "unit_number", "") or "",
        "LEASE_START_DATE": _date(getattr(lease, "start_date", None)),
        "LEASE_END_DATE": _date(getattr(lease, "end_date", None)),
        "DUE_DATE": getattr(lease, "due_date", "") or "",
        "MONTHLY_RENT": _money(getattr(lease, "monthly_rent", 0)),
        "SECURITY_DEPOSIT": _money(getattr(lease, "security_deposit", 0)),
        "BALANCE_AMOUNT": _money(_lease_balance(lease)),
        "METER_NUMBERS": _meter_numbers(unit),
        "BANK_ACCOUNT": _lease_bank_account(lease),
    }

    if request is not None and unit is not None:
        token_path = reverse("properties:unit_media_share_link", args=[unit.pk])
        context["UNIT_PHOTO_ADMIN_LINK"] = request.build_absolute_uri(token_path)

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
        "CONTACT_NUMBER": getattr(property_obj, "caretaker_phone", "") or getattr(property_obj, "owner_phone", "") or "",
        "METER_NUMBERS": _meter_numbers(unit),
        "BANK_ACCOUNT": _lease_bank_account(type("LeaseLike", (), {"unit": unit})()),
        "UNIT_PHOTO_LINK": "",
    }
    if request is not None and unit is not None:
        from properties.views import _sign_unit_media_token
        token = _sign_unit_media_token(unit.pk)
        context["UNIT_PHOTO_LINK"] = request.build_absolute_uri(
            reverse("properties:unit_media_public_share", args=[token])
        )

    for placeholder in AgreementPlaceholder.objects.filter(is_active=True):
        context.setdefault(placeholder.key, placeholder.default_value or "")
    return context


def render_whatsapp_template(template_type, lease, request=None):
    template = WhatsAppTemplate.objects.filter(
        template_type=template_type,
        is_active=True,
    ).first()
    body = template.body if template else ""
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
    for key, value in context.items():
        rendered = rendered.replace(f"[{key}]", str(value or ""))
    return template, rendered


def build_whatsapp_url(phone, message, country_code="+92"):
    normalized = normalize_whatsapp_phone(phone, country_code=country_code)
    if not normalized:
        return ""
    return f"https://wa.me/{normalized}?text={quote(message or '')}"

from django import template
from datetime import date
import re

register = template.Library()


@register.filter
def is_ending_soon(lease_end_date):
    if not lease_end_date:
        return False
    return (lease_end_date - date.today()).days <= 40


@register.filter
def whatsapp_phone(phone, country_code="+92"):
    digits = re.sub(r"\D+", "", phone or "")
    country = re.sub(r"\D+", "", country_code or "")
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and country:
        return country + digits[1:]
    return digits

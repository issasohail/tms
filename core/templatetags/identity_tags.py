from django import template

from core.utils.identity import format_cnic as format_cnic_value
from core.utils.identity import format_phone as format_phone_value
from core.utils.identity import normalize_phone
from core.utils.identity import whatsapp_phone_digits as whatsapp_phone_digits_value


register = template.Library()


@register.filter(name="format_cnic")
def format_cnic(value):
    return format_cnic_value(value)


@register.filter(name="format_phone")
def format_phone(value, country_code=""):
    return format_phone_value(value, country_code=country_code)


@register.filter(name="normalize_phone")
def normalize_phone_filter(value):
    return normalize_phone(value)


@register.filter(name="whatsapp_phone_digits")
def whatsapp_phone_digits(value, country_code=""):
    return whatsapp_phone_digits_value(value, country_code=country_code)

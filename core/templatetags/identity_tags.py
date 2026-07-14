from django import template

from core.utils.identity import format_cnic as format_cnic_value
from core.utils.identity import format_phone as format_phone_value
from core.utils.identity import normalize_phone


register = template.Library()


@register.filter(name="format_cnic")
def format_cnic(value):
    return format_cnic_value(value)


@register.filter(name="format_phone")
def format_phone(value):
    return format_phone_value(value)


@register.filter(name="normalize_phone")
def normalize_phone_filter(value):
    return normalize_phone(value)

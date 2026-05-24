from django import template

from core.currency import currency_symbol, format_money
from core.models import GlobalSettings


register = template.Library()


@register.simple_tag
def currency_symbol_tag():
    return currency_symbol(GlobalSettings.get_solo())


@register.filter
def money(value, decimals=2):
    try:
        decimals = int(decimals)
    except (TypeError, ValueError):
        decimals = 2
    return format_money(value, GlobalSettings.get_solo(), decimals=decimals)

from django import template
from django.core.cache import cache

from core.currency import currency_symbol, format_money
from core.models import GlobalSettings


register = template.Library()


def _settings_obj():
    settings_obj = cache.get("core.global_settings")
    if settings_obj is None:
        settings_obj = GlobalSettings.get_solo()
        cache.set("core.global_settings", settings_obj, 60)
    return settings_obj


@register.simple_tag
def currency_symbol_tag():
    return currency_symbol(_settings_obj())


@register.filter
def money(value, decimals=2):
    try:
        decimals = int(decimals)
    except (TypeError, ValueError):
        decimals = 2
    return format_money(value, _settings_obj(), decimals=decimals)

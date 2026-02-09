# smart_meter/templatetags/get_item.py
from django import template
register = template.Library()


@register.filter
def get_item(mapping, key):
    if mapping is None:
        return ""
    return mapping.get(key, "")

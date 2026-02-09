# templatetags/custom_filters.py
from django import template

register = template.Library()


@register.filter
def sum_attr(queryset, attr_name):
    return sum(getattr(item, attr_name) for item in queryset)


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)

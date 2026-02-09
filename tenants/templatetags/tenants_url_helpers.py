# tenants/templatetags/url_helpers.py
from django import template
from urllib.parse import urlencode

register = template.Library()


@register.simple_tag(takes_context=True)  # Add takes_context=True
def url_replace(context, field, value):
    request = context['request']
    dict_ = request.GET.copy()
    dict_[field] = value
    return urlencode(dict_)

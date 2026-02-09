# templatetags/url_helpers.py

from django import template

register = template.Library()


@register.simple_tag
def url_replace(request, **kwargs):
    """Replaces or adds GET parameters in the current URL."""
    query = request.GET.copy()
    for key, value in kwargs.items():
        if value is None:
            query.pop(key, None)  # Remove the key if value is None
        else:
            query[key] = value
    return query.urlencode()

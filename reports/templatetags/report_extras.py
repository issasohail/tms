from django import template

register = template.Library()

@register.filter
def dict_get(d, key):
    """
    Safe dictionary lookup for templates:
    {{ mydict|dict_get:somekey }}
    """
    if d is None:
        return ""
    try:
        return d.get(key, "")
    except AttributeError:
        return ""

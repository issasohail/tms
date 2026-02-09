from django import template

register = template.Library()


@register.filter
def dictkey(value, key):
    """
    Safe dict getter for templates:
      {{ mydict|dictkey:"some_key" }}
      {{ mydict|dictkey:dynamic_key }}
    Works with dicts or objects (falls back to getattr).
    """
    try:
        if isinstance(value, dict):
            return value.get(key)
        # also support defaultdict or simple objects
        return getattr(value, key, None)
    except Exception:
        return None

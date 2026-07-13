from django import template
from leases.utils import PLACEHOLDER_REGISTRY
from leases.utils import replace_db_placeholders
from django.contrib.humanize.templatetags.humanize import intcomma


register = template.Library()


# leases/templatetags/lease_tags.py

@register.filter
def replace_placeholders(text, lease):
    """Render both [KEY] and {{KEY}} placeholders using the shared resolver."""
    from leases.utils import do_replace_placeholders
    return do_replace_placeholders(text or "", lease)

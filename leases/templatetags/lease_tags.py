from django import template
from leases.utils import PLACEHOLDER_REGISTRY
from leases.utils import replace_db_placeholders
from django.contrib.humanize.templatetags.humanize import intcomma


register = template.Library()


@register.simple_tag
def agreement_signature_config():
    """Return the shared agreement/declaration signature settings."""
    from leases.models import AgreementSignatureTemplate
    return AgreementSignatureTemplate.current()


# leases/templatetags/lease_tags.py

@register.filter
def replace_placeholders(text, lease):
    """Render both [KEY] and {{KEY}} placeholders using the shared resolver."""
    from leases.utils import do_replace_placeholders
    return do_replace_placeholders(text or "", lease)

from django import template
from leases.utils import PLACEHOLDER_REGISTRY
from django.contrib.humanize.templatetags.humanize import intcomma


register = template.Library()


# leases/templatetags/lease_tags.py

@register.filter
def replace_placeholders(text, lease):
    """Replace placeholders in clause text with actual values"""
    for placeholder, func in PLACEHOLDER_REGISTRY.items():
        search_str = f'[{placeholder}]'
        if search_str in text:
            try:
                raw_value = func(lease)

                # Handle different placeholder types
                if placeholder.endswith('_IN_WORDS'):
                    replacement = f"<strong><u>{raw_value}</u></strong>"
                elif any(key in placeholder for key in ['RENT', 'DEPOSIT', 'MAINTENANCE', 'TOTAL', 'SECURITY', 'EARLY_TERMINATION_PENALTY']):
                    try:
                        # Format numbers with commas
                        num_value = int(raw_value)
                        replacement = f"<strong>{intcomma(num_value)}</strong>"
                    except (TypeError, ValueError):
                        replacement = f"<strong><u>{raw_value}</u></strong>"
                else:
                    replacement = f"<strong><u>{raw_value}</u></strong>"

                text = text.replace(search_str, replacement)
            except Exception as e:
                print(f"Error replacing {placeholder}: {e}")
                text = text.replace(search_str, f'<strong>[ERROR]</strong>')
    return text

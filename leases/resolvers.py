# resolvers.py
from django.utils import timezone


def resolve_placeholders(lease, text):
    """
    Replace placeholders in text with actual values from the lease
    """
    # Create a combined dictionary of all replacements
    replacements = {}

    # Add all PLACEHOLDER_REGISTRY values
    for placeholder, func in PLACEHOLDER_REGISTRY.items():
        # Use square brackets around placeholder
        key = f'[{placeholder}]'
        replacements[key] = str(func(lease))

    # Add any additional replacements
    additional = {
        '[SECURITY_DEPOSIT_HALF]': str(lease.security_deposit / 2),
        '[SECURITY_DEPOSIT_DUE_DATE]': (lease.start_date + timedelta(days=30)).strftime('%b %d, %Y'),
        '[TOTAL_MONTHLY]': str(lease.monthly_rent + lease.society_maintenance),
        '[SOCIETY_MAINTENANCE]': str(lease.society_maintenance),
    }
    replacements.update(additional)

    # Perform the replacements
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)

    return text

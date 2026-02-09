from django import template
from datetime import date

register = template.Library()


@register.filter
def is_ending_soon(lease_end_date):
    if not lease_end_date:
        return False
    return (lease_end_date - date.today()).days <= 40

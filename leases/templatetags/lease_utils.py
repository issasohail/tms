from django import template
from properties.models import Unit

register = template.Library()


@register.filter
def filter_by_property(value, property_id):
    if not property_id:
        return Unit.objects.none()
    return value.filter(property_id=property_id)

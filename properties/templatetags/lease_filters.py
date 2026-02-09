from django import template

register = template.Library()


@register.filter
def active_leases(leases_queryset):
    return leases_queryset.filter(status='active')

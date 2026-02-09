from django import template
from django.template.loader import render_to_string

register = template.Library()


@register.simple_tag
def export_buttons(table):
    return render_to_string('properties/includes/export_buttons.html', {'table': table})

from django import template

from whatsapp.services.whatsapp import WhatsAppService

register = template.Library()


@register.filter
def whatsapp_phone(value):
    return WhatsAppService.normalize_phone_number(value)

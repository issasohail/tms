from django import template

register = template.Library()


@register.filter
def total_payment(payments):
    return sum(payment.amount for payment in payments)

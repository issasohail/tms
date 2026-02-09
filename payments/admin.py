from django.contrib import admin
from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        'get_lease',
        'amount_display',
        'payment_date',
        'payment_method_display'
    )

    def get_lease(self, obj):
        return str(obj.lease)
    get_lease.short_description = 'Lease'

    def amount_display(self, obj):
        return f"${obj.amount:,.2f}"
    amount_display.short_description = 'Amount'

    def payment_method_display(self, obj):
        return obj.get_payment_method_display()
    payment_method_display.short_description = 'Method'

from django.contrib import admin
from .models import Property, PropertyBankAccount, Unit  # Import models from models.py only


# Method 2: Recommended way with custom admin class


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    # , 'rent_amount', 'is_occupied')
    list_display = ('property', 'unit_number', 'status',
                    "interest_type", "is_smart_meter", "electric_meter_num", "bank_account")
    list_filter = ("is_smart_meter", "property", "status", "interest_type")
    search_fields = ('unit_number', 'property__name', "electric_meter_num")
    ordering = ('property', 'unit_number')
    list_editable = ("is_smart_meter", "electric_meter_num")


class PropertyBankAccountInline(admin.TabularInline):
    model = PropertyBankAccount
    extra = 0


class PropertyAdmin(admin.ModelAdmin):
    inlines = [PropertyBankAccountInline]
    list_display = ('property_name', 'property_address1',
                    'total_units')  # , 'manager')
   # list_filter = ('manager',)
    search_fields = ('name', 'property_address1')
    fieldsets = (
        (None, {
            "fields": (
                "property_name",
                "property_type",
                "type",
                "total_units",
                "description",
            )
        }),
        ("Owner / Caretaker", {
            "fields": (
                "owner_tenant",
                "caretaker_tenant",
            )
        }),
        ("Address & Payment", {
            "fields": (
                "property_address1",
                "property_address2",
                "property_city",
                "property_state",
                "property_zipcode",
                "welcome_bank_account_mode",
            )
        }),
    )
   # prepopulated_fields = {'slug': ('name',)}  # If using slugs


admin.site.register(Property, PropertyAdmin)

from django.contrib import admin
from .models import Property, Unit  # Import models from models.py only


# Method 2: Recommended way with custom admin class


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    # , 'rent_amount', 'is_occupied')
    list_display = ('property', 'unit_number', 'status',
                    "is_smart_meter", "electric_meter_num", "use_property_bank_account")
    list_filter = ("is_smart_meter", "property", "status")
    search_fields = ('unit_number', 'property__name', "electric_meter_num")
    ordering = ('property', 'unit_number')
    list_editable = ("is_smart_meter", "electric_meter_num")


class PropertyAdmin(admin.ModelAdmin):
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
        ("Owner", {
            "fields": (
                "owner_prefix",
                "owner_name",
                "owner_father_name",
                "relation",
                "owner_cnic",
                "owner_phone",
                "owner_address",
            )
        }),
        ("Caretaker", {
            "fields": (
                "caretaker_prefix",
                "caretaker_name",
                "caretaker_father_name",
                "caretaker_relation",
                "caretaker_cnic",
                "caretaker_phone",
                "caretaker_address",
            )
        }),
        ("Address & Payment", {
            "fields": (
                "property_address1",
                "property_address2",
                "property_city",
                "property_state",
                "property_zipcode",
                "bank_account_details",
            )
        }),
    )
   # prepopulated_fields = {'slug': ('name',)}  # If using slugs


admin.site.register(Property, PropertyAdmin)

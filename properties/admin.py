from django.contrib import admin
from .models import Property, Unit  # Import models from models.py only


# Method 2: Recommended way with custom admin class


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    # , 'rent_amount', 'is_occupied')
    list_display = ('property', 'unit_number', 'status',
                    "is_smart_meter", "electric_meter_num",)
    list_filter = ("is_smart_meter", "property", "status")
    search_fields = ('unit_number', 'property__name', "electric_meter_num")
    ordering = ('property', 'unit_number')
    list_editable = ("is_smart_meter", "electric_meter_num")


class PropertyAdmin(admin.ModelAdmin):
    list_display = ('property_name', 'property_address1',
                    'total_units')  # , 'manager')
   # list_filter = ('manager',)
    search_fields = ('name', 'property_address1')
   # prepopulated_fields = {'slug': ('name',)}  # If using slugs


admin.site.register(Property, PropertyAdmin)

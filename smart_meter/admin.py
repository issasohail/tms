# smart_meter/admin.py
from django.contrib import admin
from .models import Meter, LiveReading, MeterReading, Tariff, Bill, Payment
# smart_meter/admin.py
from django.contrib import admin
from .models import UnknownMeter


@admin.register(Meter)
class MeterAdmin(admin.ModelAdmin):
    list_display = ("meter_number", "unit", "is_active", "installed_at")
    search_fields = ("meter_number", "unit__unit_number",
                     "unit__property__property_name")
    list_filter = ("is_active",)


@admin.register(LiveReading)
class LiveReadingAdmin(admin.ModelAdmin):
    list_display = (
        "meter", "ts",
        "source_ip", "source_port",
        "total_energy", "total_power", "pf_total",
        "voltage_a", "voltage_b", "voltage_c",
        "current_a", "current_b", "current_c",
        "balance",
    )
    readonly_fields = ("ts",)
    search_fields = ("meter__meter_number", "meter__unit__unit_number")
    list_filter = ("ts",)


@admin.register(MeterReading)
class MeterReadingAdmin(admin.ModelAdmin):
    list_display = (
        "meter", "ts",
        "source_ip", "source_port",
        "total_energy", "total_power", "pf_total",
        "voltage_a", "current_a",
    )
    readonly_fields = ("ts",)
    search_fields = ("meter__meter_number", "meter__unit__unit_number")
    list_filter = ("ts", "meter")


@admin.register(Tariff)
class TariffAdmin(admin.ModelAdmin):
    list_display = ("name", "rate_per_kwh", "active")
    list_filter = ("active",)
    search_fields = ("name",)


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = (
        "unit", "meter", "period_start", "period_end",
        "units_consumed", "rate_per_kwh", "amount_due", "status",
    )
    list_filter = ("status", "period_start", "period_end")
    search_fields = ("unit__unit_number", "meter__meter_number")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("bill", "amount", "date", "note")
    list_filter = ("date",)
    search_fields = ("bill__unit__unit_number", "bill__meter__meter_number")


@admin.register(UnknownMeter)
class UnknownMeterAdmin(admin.ModelAdmin):
    list_display = ("meter_number", "status", "first_seen",
                    "last_seen", "seen_count")
    search_fields = ("meter_number",)
    list_filter = ("status",)

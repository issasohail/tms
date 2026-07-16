# smart_meter/admin.py
from django.contrib import admin
from .models import (
    Meter, LiveReading, MeterReading, Tariff, Bill, Payment,
    MeterAssignmentHistory, MeterInstallation, MeterRoleHistory,
    MeterCheckGroup, MeterCheckGroupMembership,
)
# smart_meter/admin.py
from django.contrib import admin
from .models import UnknownMeter


@admin.register(Meter)
class MeterAdmin(admin.ModelAdmin):
    list_display = ("meter_number", "meter_type", "meter_role", "unit", "billing_mode", "is_active", "installed_at")
    search_fields = ("meter_number", "unit__unit_number",
                     "unit__property__property_name")
    list_filter = ("meter_type", "meter_role", "billing_mode", "is_active",)


@admin.register(MeterInstallation)
class MeterInstallationAdmin(admin.ModelAdmin):
    list_display = (
        "meter",
        "unit",
        "lease",
        "start_date",
        "end_date",
        "start_reading",
        "end_reading",
        "is_active",
    )
    list_filter = ("is_active", "start_date", "end_date", "meter__meter_type")
    search_fields = (
        "meter__meter_number",
        "unit__unit_number",
        "unit__property__property_name",
        "lease__tenant__first_name",
        "lease__tenant__last_name",
        "reason",
    )
    raw_id_fields = ("meter", "unit", "lease", "installed_by")


@admin.register(MeterRoleHistory)
class MeterRoleHistoryAdmin(admin.ModelAdmin):
    list_display = ("meter", "role", "start_date", "end_date", "is_active", "changed_by")
    list_filter = ("role", "is_active", "start_date", "end_date")
    search_fields = ("meter__meter_number", "reason")
    raw_id_fields = ("meter", "changed_by")


@admin.register(MeterCheckGroup)
class MeterCheckGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "property", "check_meter", "is_active", "created_at")
    list_filter = ("is_active", "property")
    search_fields = ("name", "property__property_name", "check_meter__meter_number")
    raw_id_fields = ("check_meter",)


@admin.register(MeterCheckGroupMembership)
class MeterCheckGroupMembershipAdmin(admin.ModelAdmin):
    list_display = ("group", "billing_meter", "start_date", "end_date", "is_active")
    list_filter = ("is_active", "start_date", "end_date", "group__property")
    search_fields = ("group__name", "billing_meter__meter_number")
    raw_id_fields = ("group", "billing_meter")


@admin.register(MeterAssignmentHistory)
class MeterAssignmentHistoryAdmin(admin.ModelAdmin):
    list_display = ("meter", "old_unit", "new_unit", "old_lease", "new_lease", "change_date", "changed_by")
    list_filter = ("change_date",)
    search_fields = ("meter__meter_number", "old_unit__unit_number", "new_unit__unit_number")


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

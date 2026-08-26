# smart_meter/admin.py
from django.contrib import admin
from .models import (
    Meter, LiveReading, MeterReading, Tariff, Bill, Payment,
    MeterAssignmentHistory, MeterInstallation, MeterRoleHistory,
    MeterCheckGroup, MeterCheckGroupMembership, MeterTimingEvent,
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
    list_display = (
        "name",
        "property",
        "covered_properties",
        "check_meter",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "property")
    search_fields = ("name", "property__property_name", "check_meter__meter_number")
    raw_id_fields = ("check_meter",)

    @admin.display(description="Covered properties")
    def covered_properties(self, obj):
        names = (
            obj.memberships.filter(
                is_active=True,
                end_date__isnull=True,
                billing_meter__is_active=True,
            )
            .values_list("billing_meter__unit__property__property_name", flat=True)
            .distinct()
            .order_by("billing_meter__unit__property__property_name")
        )
        return ", ".join(name for name in names if name) or "—"


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

# Credit-control / prepaid pilot diagnostics
from .models import (
    MeterCommand, MeterCreditAccount, MeterEvaluationRequest, MeterCreditAudit,
    MeterPrepaidPilot, MeterPrepaidParameterRead, MeterPrepaidWriteAttempt,
    MeterPrepaidRecharge,
)


@admin.register(MeterCreditAccount)
class MeterCreditAccountAdmin(admin.ModelAdmin):
    list_display = ("id", "meter", "installation", "lease", "is_enabled", "effective_credit_limit", "current_exposure", "enforcement_state", "updated_at")
    list_filter = ("is_enabled", "enforcement_state", "credit_limit_source", "automatic_cutoff", "automatic_restore")
    search_fields = ("meter__meter_number", "installation__unit__unit_number", "lease__tenant__first_name", "lease__tenant__last_name")
    raw_id_fields = ("meter", "installation", "lease", "notifications_muted_by", "enforcement_hold_by")
    readonly_fields = ("active_installation_key", "deposit_reference_amount", "effective_credit_limit", "limit_explanation", "policy_snapshot", "last_evaluated_at", "last_evaluated_reading_kwh", "accrued_usage_amount", "previous_unpaid_electricity", "payments_applied", "credits_applied", "current_exposure", "created_at", "updated_at")


@admin.register(MeterEvaluationRequest)
class MeterEvaluationRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "meter", "status", "attempts", "reading_timestamp", "created_at", "processed_at")
    list_filter = ("status", "created_at")
    search_fields = ("meter__meter_number", "last_error")
    readonly_fields = ("created_at", "updated_at", "processed_at")


@admin.register(MeterCommand)
class MeterCommandAdmin(admin.ModelAdmin):
    list_display = ("id", "meter_number", "command_type", "desired_state", "source", "status", "priority", "attempt_count", "created_at")
    list_filter = ("status", "command_type", "source", "desired_state", "created_at")
    search_fields = ("meter_number", "reason", "error", "idempotency_key")
    raw_id_fields = ("meter", "related_credit_account", "related_payment", "related_invoice", "related_enforcement_event")
    readonly_fields = ("reply_hex", "raw_ack_hex", "status_query_hex", "parsed_relay_state", "acknowledged_at", "verified_at", "created_at", "updated_at")


@admin.register(MeterCreditAudit)
class MeterCreditAuditAdmin(admin.ModelAdmin):
    list_display = ("id", "action_type", "meter", "lease", "source", "previous_state", "new_state", "exposure_after", "created_at")
    list_filter = ("action_type", "source", "created_at")
    search_fields = ("meter__meter_number", "reason", "lease__tenant__first_name", "lease__tenant__last_name")
    raw_id_fields = ("meter", "installation", "lease", "tenant", "credit_account", "invoice", "payment", "user")
    readonly_fields = ("created_at", "metadata")


@admin.register(MeterPrepaidPilot)
class MeterPrepaidPilotAdmin(admin.ModelAdmin):
    list_display = ("meter", "installation", "status", "model_name", "firmware_version", "display_balance", "updated_at")
    list_filter = ("status", "updated_at")
    search_fields = ("meter__meter_number", "model_name", "firmware_version")
    raw_id_fields = ("meter", "installation", "enabled_by")


@admin.register(MeterPrepaidParameterRead)
class MeterPrepaidParameterReadAdmin(admin.ModelAdmin):
    list_display = ("pilot", "parameter", "di", "parsed_value", "unit", "parse_status", "created_at")
    list_filter = ("parse_status", "parameter", "created_at")
    search_fields = ("pilot__meter__meter_number", "parameter", "di")
    readonly_fields = ("raw_response", "metadata", "created_at")


@admin.register(MeterPrepaidWriteAttempt)
class MeterPrepaidWriteAttemptAdmin(admin.ModelAdmin):
    list_display = ("pilot", "parameter", "requested_value", "actual_value", "status", "user", "created_at")
    list_filter = ("status", "parameter", "created_at")
    search_fields = ("pilot__meter__meter_number", "reason")
    readonly_fields = ("read_before_hex", "command_hex", "ack_hex", "read_back_hex", "created_at", "verified_at")


@admin.register(MeterPrepaidRecharge)
class MeterPrepaidRechargeAdmin(admin.ModelAdmin):
    list_display = ("transaction_id", "pilot", "amount", "before_balance", "after_balance", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("transaction_id", "pilot__meter__meter_number", "manufacturer_sequence")
    readonly_fields = ("raw_command", "raw_ack", "created_at", "updated_at")


@admin.register(MeterTimingEvent)
class MeterTimingEventAdmin(admin.ModelAdmin):
    list_display = ("meter", "weekday", "event_time", "command", "is_enabled")
    list_filter = ("weekday", "is_enabled")
    search_fields = ("meter__meter_number", "meter__unit__unit_number")

from datetime import date, timedelta
from unittest.mock import patch
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from leases.models import Lease
from properties.models import Property, Unit
from smart_meter.models import (
    LiveReading,
    Meter,
    MeterCreditAccount,
    MeterInstallation,
    MeterPrepaidPilot,
    MeterCommand,
    MeterEvaluationRequest,
)
from smart_meter.services.command_lifecycle import queue_relay_command, still_should_disconnect
from smart_meter.services.credit_control import (
    activate_credit_account,
    enforcement_held,
    evaluate_credit_account,
    notification_muted,
    resolve_effective_limit,
    set_enforcement_hold,
)
from smart_meter.services.enforcement import automatic_enforcement
from smart_meter.services.notifications import maybe_send_credit_notification
from payments.models import Payment, PaymentDetail

from smart_meter.services.prepaid_pilot import (
    PrepaidProtocolSafetyError,
    prepaid_allowlisted,
    read_supported_prepaid_snapshot,
)
from tenants.models import Tenant


class MeterCreditFixture(TestCase):
    def setUp(self):
        self.property = Property.objects.create(
            property_name="Credit Test Property",
            owner_name="Owner",
            owner_cnic="1234512345699",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(property=self.property, unit_number="C-1")
        self.tenant = Tenant.objects.create(first_name="Credit", last_name="Tenant", cnic="1234512345698")
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=date(2026, 1, 1),
            end_date=date(2027, 1, 1),
            monthly_rent=Decimal("25000.00"),
            security_deposit=Decimal("30000.00"),
        )
        self.meter = Meter.objects.create(
            meter_number="250619519998",
            unit=self.unit,
            billing_mode="credit_controlled",
            unit_rate=Decimal("50.00"),
        )
        self.installation = MeterInstallation.objects.create(
            meter=self.meter,
            unit=self.unit,
            lease=self.lease,
            start_date=date(2026, 1, 1),
            start_reading=Decimal("100.000"),
        )
        self.live = LiveReading.objects.create(meter=self.meter, total_energy=Decimal("100.000"))

    def account(self, **kwargs):
        defaults = dict(
            meter=self.meter,
            installation=self.installation,
            lease=self.lease,
            fixed_credit_limit=Decimal("1000.00"),
            warning_threshold_percent=Decimal("75.00"),
            final_warning_threshold_percent=Decimal("90.00"),
            cutoff_threshold_percent=Decimal("100.00"),
            reconnect_threshold_percent=Decimal("80.00"),
        )
        defaults.update(kwargs)
        return MeterCreditAccount.objects.create(**defaults)


class CreditLimitResolutionTests(MeterCreditFixture):
    def test_fixed_limit(self):
        account = self.account(credit_limit_source="fixed", fixed_credit_limit=Decimal("25000"))
        value, _ = resolve_effective_limit(account)
        self.assertEqual(value, Decimal("25000.00"))

    def test_deposit_derived_limit_does_not_mutate_deposit(self):
        account = self.account(credit_limit_source="deposit_percent", deposit_percentage=Decimal("50"))
        before = self.lease.security_deposit
        value, _ = resolve_effective_limit(account)
        self.lease.refresh_from_db()
        self.assertEqual(value, Decimal("15000.00"))
        self.assertEqual(self.lease.security_deposit, before)

    def test_lower_of_fixed_and_deposit(self):
        account = self.account(credit_limit_source="lower_of", fixed_credit_limit=Decimal("25000"), deposit_percentage=Decimal("100"))
        value, _ = resolve_effective_limit(account)
        self.assertEqual(value, Decimal("25000.00"))


@override_settings(
    METER_ENABLE_AUTOMATIC_CREDIT_EVALUATION=False,
    METER_ENABLE_AUTOMATIC_NOTIFICATIONS=False,
    METER_ENABLE_AUTOMATIC_CUTOFF=False,
    METER_ENABLE_AUTOMATIC_RESTORE=False,
)
class ExposureEvaluationTests(MeterCreditFixture):
    def test_incremental_usage_uses_activation_checkpoint(self):
        account = self.account()
        activate_credit_account(account, reason="test activation")
        LiveReading.objects.filter(pk=self.live.pk).update(total_energy=Decimal("110.000"), ts=timezone.now())
        result = evaluate_credit_account(account.pk)
        self.assertTrue(result.valid)
        self.assertEqual(result.exposure, Decimal("500.00"))
        self.assertEqual(result.state, "normal")

    def test_first_warning_and_cutoff_thresholds(self):
        account = self.account(fixed_credit_limit=Decimal("500.00"))
        activate_credit_account(account, reason="test activation")
        LiveReading.objects.filter(pk=self.live.pk).update(total_energy=Decimal("110.000"), ts=timezone.now())
        result = evaluate_credit_account(account.pk)
        self.assertEqual(result.exposure, Decimal("500.00"))
        self.assertEqual(result.state, "cutoff_eligible")

    def test_reading_reset_pauses_enforcement(self):
        account = self.account()
        activate_credit_account(account, reason="test activation")
        LiveReading.objects.filter(pk=self.live.pk).update(total_energy=Decimal("90.000"), ts=timezone.now())
        result = evaluate_credit_account(account.pk)
        self.assertFalse(result.valid)
        self.assertEqual(result.state, "reading_reset_detected")

    def test_huge_jump_pauses_enforcement(self):
        account = self.account(max_consumption_jump_kwh=Decimal("5.000"))
        activate_credit_account(account, reason="test activation")
        LiveReading.objects.filter(pk=self.live.pk).update(total_energy=Decimal("106.000"), ts=timezone.now())
        result = evaluate_credit_account(account.pk)
        self.assertFalse(result.valid)
        self.assertEqual(result.state, "data_review_required")

    def test_stale_reading_pauses_enforcement(self):
        account = self.account(stale_after_minutes=1)
        activate_credit_account(account, reason="test activation")
        LiveReading.objects.filter(pk=self.live.pk).update(ts=timezone.now() - timedelta(minutes=5))
        result = evaluate_credit_account(account.pk)
        self.assertFalse(result.valid)
        self.assertEqual(result.state, "stale_reading")


class MuteHoldTests(MeterCreditFixture):
    def test_notification_mute_and_hold_are_independent(self):
        account = self.account(
            notifications_muted_until=timezone.now() + timedelta(days=1),
            enforcement_hold_until=None,
        )
        self.assertTrue(notification_muted(account))
        self.assertFalse(enforcement_held(account))

    def test_current_month_mute_expires_by_month(self):
        account = self.account(
            notifications_muted_for_period="current_month",
            notification_muted_at=timezone.now(),
        )
        self.assertTrue(notification_muted(account))
        future = timezone.now() + timedelta(days=40)
        self.assertFalse(notification_muted(account, now=future))


class MeterCommandLifecycleTests(MeterCreditFixture):
    def test_duplicate_relay_command_is_consolidated(self):
        account = self.account(is_enabled=True, effective_credit_limit=Decimal("1000"), current_exposure=Decimal("1200"))
        first = queue_relay_command(self.meter, "off", source="credit_control", credit_account=account, reason="threshold")
        second = queue_relay_command(self.meter, "off", source="credit_control", credit_account=account, reason="threshold")
        self.assertEqual(first.pk, second.pk)

    def test_payment_style_on_request_cancels_unsent_automatic_off(self):
        account = self.account(is_enabled=True, effective_credit_limit=Decimal("1000"), current_exposure=Decimal("1200"))
        off = queue_relay_command(self.meter, "off", source="credit_control", credit_account=account, reason="threshold")
        queue_relay_command(self.meter, "on", source="payment", credit_account=account, reason="payment")
        off.refresh_from_db()
        self.assertEqual(off.status, "cancelled")


    def test_terminal_command_can_be_requested_again_without_idempotency_collision(self):
        account = self.account(is_enabled=True, effective_credit_limit=Decimal("1000"), current_exposure=Decimal("1200"))
        first = queue_relay_command(self.meter, "off", source="credit_control", credit_account=account, reason="threshold")
        MeterCommand.objects.filter(pk=first.pk).update(status="cancelled", cancelled_at=timezone.now())
        second = queue_relay_command(self.meter, "off", source="credit_control", credit_account=account, reason="threshold again")
        self.assertNotEqual(first.pk, second.pk)
        self.assertNotEqual(first.idempotency_key, second.idempotency_key)

    @override_settings(
        METER_ENABLE_AUTOMATIC_CREDIT_EVALUATION=True,
        METER_ENABLE_AUTOMATIC_CUTOFF=True,
        METER_AUTOMATIC_CUTOFF_PROTECTED_START="00:00",
        METER_AUTOMATIC_CUTOFF_PROTECTED_END="00:00",
    )
    def test_current_month_hold_blocks_deferred_automatic_off(self):
        account = self.account(
            is_enabled=True, effective_credit_limit=Decimal("1000"), current_exposure=Decimal("1200"),
            enforcement_hold_for_period="current_month", enforcement_hold_at=timezone.now(),
        )
        cmd = queue_relay_command(self.meter, "off", source="credit_control", credit_account=account, reason="threshold")
        with self.settings(METER_CREDIT_ALLOWED_METER_IDS=(self.meter.pk,)):
            result = still_should_disconnect(cmd)
        self.assertFalse(result.allowed)
        self.assertIn("hold", result.reason)

    @override_settings(
        METER_ENABLE_AUTOMATIC_CREDIT_EVALUATION=True,
        METER_ENABLE_AUTOMATIC_CUTOFF=True,
        METER_AUTOMATIC_CUTOFF_PROTECTED_START="00:00",
        METER_AUTOMATIC_CUTOFF_PROTECTED_END="00:00",
    )
    def test_stale_reading_blocks_deferred_automatic_off(self):
        account = self.account(is_enabled=True, effective_credit_limit=Decimal("1000"), current_exposure=Decimal("1200"), stale_after_minutes=1)
        LiveReading.objects.filter(pk=self.live.pk).update(ts=timezone.now() - timedelta(minutes=5))
        cmd = queue_relay_command(self.meter, "off", source="credit_control", credit_account=account, reason="threshold")
        with self.settings(METER_CREDIT_ALLOWED_METER_IDS=(self.meter.pk,)):
            result = still_should_disconnect(cmd)
        self.assertFalse(result.allowed)
        self.assertIn("stale", result.reason)

    @override_settings(
        METER_ENABLE_AUTOMATIC_CREDIT_EVALUATION=True,
        METER_ENABLE_AUTOMATIC_CUTOFF=True,
        METER_CREDIT_ALLOWED_METER_IDS=(),
        METER_AUTOMATIC_CUTOFF_PROTECTED_START="00:00",
        METER_AUTOMATIC_CUTOFF_PROTECTED_END="00:00",
    )
    def test_non_allowlisted_meter_blocks_deferred_automatic_off(self):
        account = self.account(is_enabled=True, effective_credit_limit=Decimal("1000"), current_exposure=Decimal("1200"))
        cmd = queue_relay_command(self.meter, "off", source="credit_control", credit_account=account, reason="threshold")
        result = still_should_disconnect(cmd)
        self.assertFalse(result.allowed)
        self.assertIn("ALLOWED_METER_IDS", result.reason)

    @override_settings(
        METER_ENABLE_AUTOMATIC_CREDIT_EVALUATION=True,
        METER_ENABLE_AUTOMATIC_CUTOFF=True,
        METER_ENABLE_AUTOMATIC_RESTORE=True,
    )
    def test_sent_off_with_payment_queues_compensating_on(self):
        account = self.account(
            is_enabled=True, effective_credit_limit=Decimal("1000"), current_exposure=Decimal("100"),
            automatic_restore=True, enforcement_state="normal",
        )
        off = queue_relay_command(self.meter, "off", source="credit_control", credit_account=account, reason="threshold")
        MeterCommand.objects.filter(pk=off.pk).update(status="sent")
        with self.settings(METER_CREDIT_ALLOWED_METER_IDS=(self.meter.pk,)):
            cmd = automatic_enforcement(account.pk)
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.desired_state, "on")
        self.assertEqual(cmd.source, "payment")
        off.refresh_from_db()
        self.assertEqual(off.status, "cancelled")

    @override_settings(METER_ENABLE_AUTOMATIC_CUTOFF=False)
    def test_deferred_automatic_off_revalidates_feature_switch(self):
        account = self.account(is_enabled=True, effective_credit_limit=Decimal("1000"), current_exposure=Decimal("1200"))
        cmd = queue_relay_command(self.meter, "off", source="credit_control", credit_account=account, reason="threshold")
        result = still_should_disconnect(cmd)
        self.assertFalse(result.allowed)
        self.assertIn("feature switch", result.reason)




class PaymentAndNotificationIntegrationTests(MeterCreditFixture):
    def test_payment_detail_change_queues_evaluation(self):
        self.account(is_enabled=True)
        with self.captureOnCommitCallbacks(execute=True):
            payment = Payment.objects.create(lease=self.lease, amount=Decimal("100.00"))
            PaymentDetail.objects.create(payment=payment, lease_amount=Decimal("100.00"))
        self.assertEqual(MeterEvaluationRequest.objects.filter(meter=self.meter, status="pending").count(), 1)

    @override_settings(METER_ENABLE_AUTOMATIC_NOTIFICATIONS=True)
    @patch("smart_meter.services.notifications.WhatsAppService.send_text")
    def test_notification_mute_suppresses_whatsapp(self, send_text):
        account = self.account(
            is_enabled=True, effective_credit_limit=Decimal("1000"), current_exposure=Decimal("800"),
            enforcement_state="warning_1", notifications_muted_until=timezone.now() + timedelta(days=1),
        )
        result = maybe_send_credit_notification(account, "warning_1")
        self.assertFalse(result["sent"])
        self.assertIn("muted", result["reason"])
        send_text.assert_not_called()

    def test_enforcement_hold_cancels_unsent_automatic_off(self):
        account = self.account(is_enabled=True, effective_credit_limit=Decimal("1000"), current_exposure=Decimal("1200"))
        off = queue_relay_command(self.meter, "off", source="credit_control", credit_account=account, reason="threshold")
        user = get_user_model().objects.create_superuser(username="credit-admin", password="x", email="credit@example.com")
        set_enforcement_hold(account.pk, user=user, reason="payment arrangement", until=timezone.now() + timedelta(days=1))
        off.refresh_from_db()
        self.assertEqual(off.status, "cancelled")
        self.assertIn("hold", off.cancelled_reason)


@override_settings(METER_ENABLE_PREPAID_READS=True, METER_PREPAID_ALLOWED_METER_IDS=(1,))
class PrepaidPilotSafetyTests(MeterCreditFixture):
    def test_legacy_prepaid_value_is_not_reinterpreted(self):
        self.meter.billing_mode = "prepaid"
        self.meter.save(update_fields=["billing_mode"])
        self.assertTrue(self.meter.is_prepaid)
        self.assertNotEqual(self.meter.billing_mode, "prepaid_pilot")

    @override_settings(METER_ENABLE_PREPAID_READS=False, METER_PREPAID_ALLOWED_METER_IDS=())
    def test_prepaid_read_requires_switch_and_allowlist(self):
        self.meter.billing_mode = "prepaid_pilot"
        self.meter.save(update_fields=["billing_mode"])
        with self.assertRaises(PrepaidProtocolSafetyError):
            read_supported_prepaid_snapshot(self.meter.pk)


class ManufacturerStatusWordTests(TestCase):
    def test_documented_bit8_reports_closed_relay_as_on(self):
        from smart_meter.dlt645 import relay_state_from_status_word
        self.assertEqual(relay_state_from_status_word("0000"), "on")

    def test_documented_bit8_reports_tripped_relay_as_off(self):
        from smart_meter.dlt645 import relay_state_from_status_word
        self.assertEqual(relay_state_from_status_word("0100"), "off")

    def test_documented_bit15_power_protection_flag(self):
        from smart_meter.dlt645 import power_protection_from_status_word
        self.assertTrue(power_protection_from_status_word("8000"))
        self.assertFalse(power_protection_from_status_word("0000"))

"""Offline regression tests for prepaid money safety and reconciliation."""
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from smart_meter.dlt645 import _add_33, build_frame
from smart_meter.management.commands.meter_listener import (
    DbCommandPoller,
    _deliver_if_match,
    _register_handler,
    _unregister_handler,
)
from smart_meter.models import (
    LiveReading,
    Meter,
    MeterCommand,
    MeterPrepaidPilot,
    MeterPrepaidRecharge,
)
from smart_meter.services.prepaid_money import (
    CONSUMED_MANUFACTURER_ORDERS,
    UNCERTAIN_OPERATOR_MESSAGE,
    acknowledge_late_prepaid_reply,
    generate_prepaid_order_number,
    queue_prepaid_money_transaction,
    reconcile_prepaid_balance,
)
from smart_meter.utils.db_send import send_via_db


METER_NUMBER = "260305510012"
ZERO_BALANCE_028011FF = bytes.fromhex(
    "681200510503266891453244B3353333333333333333775733333333AC3533333333333333"
    "6C35336C3533333333333333893689363333333396A7333396A73333333333333333333333"
    "33333333334116"
)


def money_ack(di: str) -> bytes:
    return build_frame(
        METER_NUMBER,
        0x83,
        _add_33(bytes.fromhex(di)[::-1]),
        checksum_mode="incl_1st68",
    )


class ImmediateMoneyHandler:
    peer = "test:6000"

    def __init__(self, meter_number, *, di=None, transport=True, status_reply=None):
        self.meter_number = meter_number
        self.di = di
        self.transport = transport
        self.status_reply = status_reply
        self.frames = []

    def enqueue_send(self, frame, expire_at=None, transport_q=None):
        self.frames.append(frame.hex().upper())
        if self.transport is True:
            transport_q.put_nowait((True, ""))
        elif self.transport is False:
            transport_q.put_nowait((False, "socket disconnected during send"))
        if self.di and len(self.frames) == 1:
            _deliver_if_match(
                self.meter_number, self.di, 0x83, money_ack(self.di)
            )
        elif self.status_reply is not None and len(self.frames) == 2:
            _deliver_if_match(
                self.meter_number, "028011FF", 0x91, self.status_reply
            )

    def close(self, reason="shutdown"):
        return None


class PrepaidMoneyLifecycleTests(TestCase):
    def setUp(self):
        self.meter = Meter.objects.create(meter_number=METER_NUMBER)
        self.pilot = MeterPrepaidPilot.objects.create(
            meter=self.meter, status="active_test"
        )
        LiveReading.objects.create(meter=self.meter, balance=Decimal("0.00"))
        self.poller = DbCommandPoller(interval=0)

    def queue(self, *, operation="recharge", order="1240826202124150", amount="1.00"):
        return queue_prepaid_money_transaction(
            meter=self.meter,
            operation=operation,
            amount=Decimal(amount),
            order_number=order,
            timeout=0.01,
            reason="offline lifecycle test",
        )

    def test_recharge_uses_expected_di_and_single_attempt(self):
        prepaid, command = self.queue()

        self.assertEqual(command.expect_di, "070102FF")
        self.assertEqual(command.command_type, "prepaid_recharge")
        self.assertEqual(command.max_attempts, 1)
        self.assertTrue(command.requires_verification)
        self.assertEqual(prepaid.transaction_id, "1240826202124150")

    def test_c83_recharge_reply_is_meter_acknowledgement_not_reconciliation(self):
        prepaid, command = self.queue()
        handler = ImmediateMoneyHandler(METER_NUMBER, di="070102FF")

        with patch(
            "smart_meter.management.commands.meter_listener._get_handler",
            return_value=handler,
        ):
            self.poller._process_command(command)

        command.refresh_from_db()
        prepaid.refresh_from_db()
        self.assertEqual(command.status, "acknowledged")
        self.assertEqual(command.raw_ack_hex, money_ack("070102FF").hex().upper())
        self.assertEqual(prepaid.status, "uncertain")
        self.assertIn("Do not retry", prepaid.reconciliation_note)
        self.assertEqual(len(handler.frames), 2)

    def test_transport_timeout_becomes_uncertain_without_requeue(self):
        prepaid, command = self.queue()
        handler = ImmediateMoneyHandler(METER_NUMBER, transport=None)

        with patch(
            "smart_meter.management.commands.meter_listener._get_handler",
            return_value=handler,
        ):
            self.poller._process_command(command)

        command.refresh_from_db()
        prepaid.refresh_from_db()
        self.assertEqual(command.status, "sent")
        self.assertEqual(command.attempt_count, 1)
        self.assertIsNone(command.next_attempt_at)
        self.assertEqual(prepaid.status, "uncertain")
        self.assertIn("Do not retry", command.error)
        self.assertEqual(len(handler.frames), 1)
        self.assertFalse(
            MeterCommand.objects.filter(
                pk=command.pk,
                status__in=("new", "pending", "retry", "waiting_online"),
            ).exists()
        )

    def test_disconnect_ambiguity_is_not_retried(self):
        _prepaid, command = self.queue()
        handler = ImmediateMoneyHandler(METER_NUMBER, transport=False)

        with patch(
            "smart_meter.management.commands.meter_listener._get_handler",
            return_value=handler,
        ):
            self.poller._process_command(command)

        command.refresh_from_db()
        self.assertEqual(command.status, "sent")
        self.assertEqual(command.attempt_count, 1)
        self.assertEqual(len(handler.frames), 1)
        self.assertIn("Do not retry", command.error)

    def test_late_c83_ack_recovers_an_uncertain_recharge(self):
        prepaid, command = self.queue()
        MeterCommand.objects.filter(pk=command.pk).update(
            status="sent", attempt_count=1
        )

        matched = acknowledge_late_prepaid_reply(
            METER_NUMBER, "070102FF", 0x83, money_ack("070102FF")
        )

        command.refresh_from_db()
        prepaid.refresh_from_db()
        self.assertEqual(matched.pk, command.pk)
        self.assertEqual(command.status, "acknowledged")
        self.assertEqual(prepaid.status, "pending")
        self.assertTrue(prepaid.raw_ack)

    def test_duplicate_and_physically_consumed_orders_are_rejected(self):
        self.queue(order="1240826202124150")
        with self.assertRaisesMessage(ValueError, "already exists"):
            self.queue(order="1240826202124150")

        self.assertEqual(
            CONSUMED_MANUFACTURER_ORDERS,
            {"1240826202124140", "1240826202124141"},
        )
        for consumed in CONSUMED_MANUFACTURER_ORDERS:
            with self.assertRaisesMessage(ValueError, "already been consumed"):
                self.queue(order=consumed)
        self.assertEqual(MeterCommand.objects.count(), 1)

    def test_order_generator_checks_persisted_orders_across_processes(self):
        MeterPrepaidRecharge.objects.create(
            pilot=self.pilot,
            transaction_id="000000000000000A",
            manufacturer_sequence="000000000000000A",
            amount=Decimal("1.00"),
            status="verified",
        )
        with patch(
            "smart_meter.services.prepaid_money.time.time_ns",
            side_effect=[0xA, 0xB],
        ):
            generated = generate_prepaid_order_number()

        self.assertEqual(generated, "000000000000000B")

    def test_acknowledged_recharge_reconciles_on_authoritative_balance(self):
        prepaid, command = self.queue()
        handler = ImmediateMoneyHandler(METER_NUMBER, di="070102FF")
        with patch(
            "smart_meter.management.commands.meter_listener._get_handler",
            return_value=handler,
        ):
            self.poller._process_command(command)

        reconciled = reconcile_prepaid_balance(self.meter, Decimal("1.00"))

        command.refresh_from_db()
        prepaid.refresh_from_db()
        self.assertEqual(reconciled, [prepaid])
        self.assertEqual(prepaid.status, "verified")
        self.assertEqual(prepaid.before_balance, Decimal("0.00"))
        self.assertEqual(prepaid.after_balance, Decimal("1.00"))
        self.assertEqual(command.status, "verified")

    def test_acknowledged_refund_reconciles_on_authoritative_balance(self):
        LiveReading.objects.filter(meter=self.meter).update(balance=Decimal("10.00"))
        prepaid, command = self.queue(
            operation="refund", order="1240826202124142", amount="1.00"
        )
        handler = ImmediateMoneyHandler(METER_NUMBER, di="070108FF")
        with patch(
            "smart_meter.management.commands.meter_listener._get_handler",
            return_value=handler,
        ):
            self.poller._process_command(command)

        reconcile_prepaid_balance(self.meter, Decimal("9.00"))

        command.refresh_from_db()
        prepaid.refresh_from_db()
        self.assertEqual(command.expect_di, "070108FF")
        self.assertEqual(command.status, "verified")
        self.assertEqual(prepaid.status, "verified")
        self.assertEqual(prepaid.before_balance, Decimal("10.00"))
        self.assertEqual(prepaid.after_balance, Decimal("9.00"))

    def test_full_wallet_refund_verifies_only_at_zero(self):
        LiveReading.objects.filter(meter=self.meter).update(balance=Decimal("10.00"))
        prepaid, command = self.queue(
            operation="refund", order="1240826202124144", amount="10.00"
        )
        command.status = "acknowledged"
        command.raw_ack_hex = money_ack("070108FF").hex().upper()
        command.save(update_fields=["status", "raw_ack_hex", "updated_at"])
        prepaid.raw_ack = command.raw_ack_hex
        prepaid.save(update_fields=["raw_ack", "updated_at"])

        reconcile_prepaid_balance(self.meter, Decimal("0.00"))

        command.refresh_from_db()
        prepaid.refresh_from_db()
        self.assertEqual(command.status, "verified")
        self.assertEqual(prepaid.status, "verified")
        self.assertEqual(prepaid.after_balance, Decimal("0.00"))

    def test_refund_ack_triggers_fresh_balance_read_and_verifies_zero(self):
        LiveReading.objects.filter(meter=self.meter).update(balance=Decimal("1.00"))
        prepaid, command = self.queue(
            operation="refund", order="1240826202124146", amount="1.00"
        )
        handler = ImmediateMoneyHandler(
            METER_NUMBER,
            di="070108FF",
            status_reply=ZERO_BALANCE_028011FF,
        )

        with patch(
            "smart_meter.management.commands.meter_listener._get_handler",
            return_value=handler,
        ):
            self.poller._process_command(command)

        command.refresh_from_db()
        prepaid.refresh_from_db()
        self.assertEqual(len(handler.frames), 2)
        self.assertTrue(command.status_query_hex)
        self.assertEqual(command.status, "verified")
        self.assertEqual(prepaid.status, "verified")
        self.assertEqual(prepaid.after_balance, Decimal("0.00"))

    def test_refund_balance_mismatch_stays_uncertain_without_retry(self):
        LiveReading.objects.filter(meter=self.meter).update(balance=Decimal("10.00"))
        prepaid, command = self.queue(
            operation="refund", order="1240826202124145", amount="10.00"
        )
        command.status = "acknowledged"
        command.raw_ack_hex = money_ack("070108FF").hex().upper()
        command.save(update_fields=["status", "raw_ack_hex", "updated_at"])
        prepaid.raw_ack = command.raw_ack_hex
        prepaid.save(update_fields=["raw_ack", "updated_at"])

        reconcile_prepaid_balance(self.meter, Decimal("0.01"))

        command.refresh_from_db()
        prepaid.refresh_from_db()
        self.assertEqual(command.status, "acknowledged")
        self.assertEqual(command.max_attempts, 1)
        self.assertEqual(prepaid.status, "uncertain")
        self.assertIn("do not retry", prepaid.reconciliation_note)

    def test_uncertain_api_result_is_never_presented_as_retryable(self):
        _prepaid, command = self.queue()
        MeterCommand.objects.filter(pk=command.pk).update(
            status="sent", error=UNCERTAIN_OPERATOR_MESSAGE, attempt_count=1
        )
        command.refresh_from_db()

        with patch(
            "smart_meter.services.prepaid_money.reserve_prepaid_money_command",
            return_value=command,
        ), patch("smart_meter.utils.db_send.time.sleep", return_value=None):
            result = send_via_db(
                meter_number=METER_NUMBER,
                frame_hex=command.frame_hex,
                command_type="prepaid_recharge",
                expect_di="070102FF",
                timeout=0.01,
            )

        self.assertFalse(result["ok"])
        self.assertFalse(result["retryable"])
        self.assertTrue(result["verification_required"])
        self.assertIn("Do not retry", result["error"])

    def test_reconnect_does_not_wake_ambiguous_money_command(self):
        _prepaid, command = self.queue()
        MeterCommand.objects.filter(pk=command.pk).update(
            status="waiting_online", attempt_count=1
        )
        handler = ImmediateMoneyHandler(METER_NUMBER)
        try:
            _register_handler(METER_NUMBER, handler)
            command.refresh_from_db()
        finally:
            _unregister_handler(METER_NUMBER, handler)

        self.assertEqual(command.status, "sent")
        self.assertIsNone(command.next_attempt_at)

    def test_legacy_retry_is_suppressed_before_poller_can_enqueue_again(self):
        prepaid, command = self.queue()
        MeterCommand.objects.filter(pk=command.pk).update(
            status="retry", attempt_count=1, next_attempt_at=None
        )
        handler = ImmediateMoneyHandler(METER_NUMBER)
        self.poller._stop = MagicMock()
        self.poller._stop.is_set.side_effect = [False, True]
        self.poller._stop.wait.return_value = True

        with patch(
            "smart_meter.management.commands.meter_listener._get_handler",
            return_value=handler,
        ), patch(
            "smart_meter.management.commands.meter_listener.close_old_connections"
        ):
            self.poller.run()

        command.refresh_from_db()
        prepaid.refresh_from_db()
        self.assertEqual(command.status, "sent")
        self.assertEqual(prepaid.status, "uncertain")
        self.assertEqual(handler.frames, [])

    def test_ordinary_non_money_retry_behavior_is_unchanged(self):
        command = MeterCommand.objects.create(
            meter=self.meter,
            meter_number=METER_NUMBER,
            frame_hex="68",
            command_type="read",
            status="claimed",
            attempt_count=1,
            max_attempts=5,
        )

        self.poller._retry_or_fail(command, "ordinary read timeout")

        command.refresh_from_db()
        self.assertEqual(command.status, "retry")
        self.assertIsNotNone(command.next_attempt_at)

    def test_pre_enqueue_failure_is_definitive_not_uncertain(self):
        prepaid, command = self.queue()

        self.poller._fail(command, "invalid frame before enqueue")

        command.refresh_from_db()
        prepaid.refresh_from_db()
        self.assertEqual(command.status, "failed")
        self.assertEqual(prepaid.status, "failed")
        self.assertIn("Definitive failure", prepaid.reconciliation_note)

    def test_wrong_recharge_di_is_rejected_before_enqueue(self):
        prepaid, command = self.queue(
            operation="refund", order="1240826202124143"
        )
        MeterCommand.objects.filter(pk=command.pk).delete()
        MeterPrepaidRecharge.objects.filter(pk=prepaid.pk).delete()

        with self.assertRaisesMessage(ValueError, "expect_di 070102FF"):
            send_via_db(
                meter_number=METER_NUMBER,
                frame_hex=command.frame_hex,
                command_type="prepaid_recharge",
                expect_di="070108FF",
                timeout=0.01,
            )
        self.assertEqual(MeterCommand.objects.count(), 0)

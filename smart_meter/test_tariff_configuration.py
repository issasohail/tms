import importlib
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from smart_meter.forms_tariff import TariffConfigurationForm
from smart_meter.models import (
    Meter,
    MeterTariffAudit,
    MeterTariffBulkRun,
    MeterTariffConfiguration,
)
from smart_meter.services.tariff_configuration import (
    configure_prices,
    read_current_configuration,
    save_schedule_draft,
)
from smart_meter.services.tariff_protocol import (
    MULTI_RATE_COUNT_OFFSET,
    MULTI_SET1_PRICE_OFFSETS,
    SINGLE_PRICE_OFFSETS,
    TariffProtocolError,
    build_tariff_write_frame,
    decode_payload,
    encode_price,
    mutate_payload,
    target_byte_indexes,
)


def payload_for(capability, prices=("1.0000", "2.0000", "3.0000", "4.0000"), count=4):
    length = 63 if capability == "single_rate" else 143
    payload = bytearray([0x33] * length)
    offsets = SINGLE_PRICE_OFFSETS if capability == "single_rate" else MULTI_SET1_PRICE_OFFSETS
    for offset, price in zip(offsets, prices):
        payload[offset:offset + 4] = encode_price(price)
    if capability == "multi_rate":
        payload[MULTI_RATE_COUNT_OFFSET] = 0x33 + count
    return bytes(payload)


def ack_frame(meter_number="123456789012"):
    inner = b"\x68" + bytes.fromhex(meter_number)[::-1] + b"\x68\x83\x00"
    return (inner + bytes([sum(inner) & 0xFF, 0x16])).hex().upper()


class TariffCodecTests(SimpleTestCase):
    def test_single_rate_changes_only_four_tariff_slots(self):
        before = payload_for("single_rate")
        after = mutate_payload(before, "single_rate", prices=[Decimal("40.0000")], flat=True)
        changed = {index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1]}
        self.assertTrue(changed)
        self.assertLessEqual(changed, target_byte_indexes("single_rate", flat=True))
        self.assertEqual(decode_payload(after, "single_rate")["prices"], [Decimal("40.0000")] * 4)
        frame = build_tariff_write_frame("260305510012", "single_rate", after)
        self.assertEqual(frame[12], 0x03)
        self.assertEqual(frame[13], 0x47)

    def test_multi_flat_changes_only_count_and_set1_prices(self):
        before = payload_for("multi_rate")
        after = mutate_payload(before, "multi_rate", prices=["40.0000"], active_rate_count=1, flat=True)
        changed = {index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1]}
        self.assertLessEqual(changed, target_byte_indexes("multi_rate", 1, flat=True))
        values = decode_payload(after, "multi_rate")
        self.assertEqual(values["active_rate_count"], 1)
        self.assertEqual(values["prices"], [Decimal("40.0000")] * 4)

    def test_multi_tou_preserves_inactive_prices_and_unrelated_bytes(self):
        before = payload_for("multi_rate")
        after = mutate_payload(
            before, "multi_rate", prices=["10.0000", "20.0000", "30.0000"],
            active_rate_count=3,
        )
        self.assertEqual(after[MULTI_SET1_PRICE_OFFSETS[3]:MULTI_SET1_PRICE_OFFSETS[3] + 4], encode_price("4.0000"))
        allowed = target_byte_indexes("multi_rate", 3)
        self.assertTrue(all(a == b for index, (a, b) in enumerate(zip(before, after)) if index not in allowed))

    def test_stored_identifier_is_not_globally_restricted_but_wire_address_is_safe(self):
        before = payload_for("single_rate")
        with self.assertRaises(TariffProtocolError):
            build_tariff_write_frame("CUSTOM-METER", "single_rate", before)


class ScheduleValidationTests(SimpleTestCase):
    def form(self, schedule, **overrides):
        data = {
            "mode": "time_of_use", "active_rate_count": "3", "schedule_json": schedule,
            "rate_1_label": "Valley", "rate_1_price": "20.0000",
            "rate_2_label": "Flat", "rate_2_price": "30.0000",
            "rate_3_label": "Peak", "rate_3_price": "40.0000",
        }
        data.update(overrides)
        meter = Meter(meter_number="anything", tariff_capability="multi_rate")
        return TariffConfigurationForm(data=data, meter=meter)

    def test_valid_full_day_including_overnight(self):
        form = self.form('[{"start":"23:00","end":"07:00","rate":1},{"start":"07:00","end":"17:00","rate":2},{"start":"17:00","end":"23:00","rate":3}]')
        self.assertTrue(form.is_valid(), form.errors)

    def test_rejects_gap_overlap_invalid_time_and_missing_rate(self):
        bad_values = [
            '[{"start":"00:00","end":"10:00","rate":1},{"start":"11:00","end":"00:00","rate":2}]',
            '[{"start":"00:00","end":"13:00","rate":1},{"start":"12:00","end":"00:00","rate":2}]',
            '[{"start":"25:00","end":"00:00","rate":1}]',
        ]
        for value in bad_values:
            self.assertFalse(self.form(value).is_valid())
        self.assertFalse(self.form('[{"start":"00:00","end":"00:00","rate":1}]', rate_2_label="").is_valid())


class TariffWorkflowTests(TestCase):
    def setUp(self):
        self.meter = Meter.objects.create(meter_number="123456789012", tariff_capability="multi_rate")
        self.user = get_user_model().objects.create_superuser(
            "tariff-admin", "pw", email="t@example.com"
        )
        self.before = payload_for("multi_rate", prices=("40", "40", "40", "40"), count=1)

    @patch("smart_meter.services.tariff_configuration.send_via_db")
    @patch("smart_meter.services.tariff_configuration._send_read")
    def test_no_change_sends_no_write_and_is_audited(self, read, send):
        read.return_value = (self.before, "AA", 10)
        audit = configure_prices(
            meter=self.meter, user=self.user, mode="flat", prices=["40"], active_rate_count=1
        )
        self.assertEqual(audit.status, "no_change")
        send.assert_not_called()
        self.assertEqual(audit.command_ids, [10])

    @patch("smart_meter.services.tariff_configuration.send_via_db")
    def test_offline_read_is_clear_audited_and_never_retried(self, send):
        send.return_value = {
            "ok": False, "status": "failed", "error": "meter not connected",
            "command_id": 8,
        }
        audit = read_current_configuration(meter=self.meter, user=self.user)
        self.assertEqual(audit.status, "offline_queued")
        self.assertIn("not connected", audit.error)
        self.assertEqual(audit.command_ids, [8])
        self.assertTrue(MeterTariffAudit.objects.filter(pk=audit.pk).exists())
        self.assertEqual(send.call_args.kwargs["max_attempts"], 1)

    @patch("smart_meter.services.tariff_configuration.send_via_db")
    def test_malformed_read_reply_is_retained_in_audit(self, send):
        send.return_value = {
            "ok": True, "status": "ok", "reply": "680102", "command_id": 9,
        }
        audit = read_current_configuration(meter=self.meter, user=self.user)
        self.assertEqual(audit.status, "failed")
        self.assertEqual(audit.raw_read_back_frame, "680102")
        self.assertEqual(audit.command_ids, [9])

    @patch("smart_meter.services.tariff_configuration.send_via_db")
    @patch("smart_meter.services.tariff_configuration._send_read")
    def test_duplicate_submission_key_does_not_repeat_work(self, read, send):
        read.return_value = (self.before, "AA", 10)
        submission_key = uuid.uuid4()
        first = configure_prices(
            meter=self.meter, user=self.user, mode="flat", prices=["40"],
            active_rate_count=1, submission_key=submission_key,
        )
        second = configure_prices(
            meter=self.meter, user=self.user, mode="flat", prices=["40"],
            active_rate_count=1, submission_key=submission_key,
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(read.call_count, 1)
        send.assert_not_called()

    def test_valid_schedule_is_saved_as_draft_without_transport(self):
        rows = [
            {"start": "23:00", "end": "07:00", "rate": 1},
            {"start": "07:00", "end": "17:00", "rate": 2},
            {"start": "17:00", "end": "23:00", "rate": 3},
        ]
        audit = save_schedule_draft(
            meter=self.meter, user=self.user, rows=rows,
            labels=["Valley", "Flat", "Peak", "Shoulder"], active_rate_count=3,
        )
        config = MeterTariffConfiguration.objects.get(meter=self.meter)
        self.assertEqual(audit.status, "draft")
        self.assertEqual(config.schedule_draft, rows)

    @patch("smart_meter.services.tariff_configuration.send_via_db")
    @patch("smart_meter.services.tariff_configuration._send_read")
    def test_transport_delivery_without_readback_is_not_verified(self, read, send):
        changed = mutate_payload(self.before, "multi_rate", prices=["50"], active_rate_count=1, flat=True)
        read.side_effect = [(self.before, "AA", 1), TariffProtocolError("read-back timed out")]
        send.return_value = {"ok": True, "status": "acknowledged", "reply": ack_frame(), "command_id": 2}
        audit = configure_prices(
            meter=self.meter, user=self.user, mode="flat", prices=["50"], active_rate_count=1
        )
        self.assertEqual(audit.status, "sent_pending_verification")
        self.assertNotEqual(audit.status, "verified")
        self.assertTrue(changed)

    @patch("smart_meter.services.tariff_configuration.send_via_db")
    @patch("smart_meter.services.tariff_configuration._send_read")
    def test_exact_readback_verifies_and_unrelated_change_fails(self, read, send):
        after = mutate_payload(self.before, "multi_rate", prices=["50"], active_rate_count=1, flat=True)
        send.return_value = {"ok": True, "status": "acknowledged", "reply": ack_frame(), "command_id": 2}
        read.side_effect = [(self.before, "AA", 1), (after, "BB", 3)]
        audit = configure_prices(meter=self.meter, user=self.user, mode="flat", prices=["50"], active_rate_count=1)
        self.assertEqual(audit.status, "verified")
        unsafe = bytearray(after); unsafe[90] = 0x34
        read.side_effect = [(self.before, "AA", 4), (bytes(unsafe), "CC", 6)]
        audit = configure_prices(meter=self.meter, user=self.user, mode="flat", prices=["50"], active_rate_count=1)
        self.assertEqual(audit.status, "unsafe_readback")


class TariffUiAndMigrationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "ui-admin", "pw", email="ui@example.com"
        )
        self.client.force_login(self.user)
        self.single = Meter.objects.create(meter_number="26-ANYTHING", tariff_capability="single_rate")
        self.multi = Meter.objects.create(meter_number="ABC", tariff_capability="multi_rate")

    def test_capability_is_visible_in_add_edit_detail_and_list_and_filters(self):
        self.assertContains(self.client.get(reverse("smart_meter:add_meter")), "Tariff Capability")
        self.assertContains(self.client.get(reverse("smart_meter:meter_edit", args=[self.single.pk])), "Single-rate")
        self.assertContains(self.client.get(reverse("smart_meter:meter_detail", args=[self.single.pk])), "Tariff Configuration")
        response = self.client.get(reverse("smart_meter:meter_list"), {"tariff": "single_rate"})
        self.assertContains(response, "26-ANYTHING")
        self.assertNotContains(response, ">ABC<")

    def test_schedule_write_is_disabled_and_draft_message_is_present(self):
        response = self.client.get(reverse("smart_meter:tariff_configure", args=[self.multi.pk]))
        self.assertContains(response, "vendor-confirmed 070105FF byte mapping")
        self.assertContains(response, "Write Schedule to Meter")
        self.assertContains(response, "disabled")

    def test_data_migration_classifies_only_valid_wire_meter_numbers(self):
        invalid = Meter.objects.create(
            meter_number="INVALID-METER",
            tariff_capability="multi_rate",
        )
        Meter.objects.filter(pk=self.single.pk).update(tariff_capability="unknown")
        Meter.objects.filter(pk=self.multi.pk).update(tariff_capability="unknown")
        migration = importlib.import_module(
            "smart_meter.migrations.0037_correct_invalid_tariff_capabilities"
        )
        migration.classify_valid_meter_tariffs(apps, None)
        self.single.refresh_from_db()
        self.multi.refresh_from_db()
        invalid.refresh_from_db()
        self.assertEqual(self.single.tariff_capability, "unknown")
        self.assertEqual(self.multi.tariff_capability, "unknown")
        self.assertEqual(invalid.tariff_capability, "unknown")

        valid_single = Meter.objects.create(
            meter_number="260305510001",
            tariff_capability="unknown",
        )
        valid_multi = Meter.objects.create(
            meter_number="123456789012",
            tariff_capability="unknown",
        )
        migration.classify_valid_meter_tariffs(apps, None)
        valid_single.refresh_from_db()
        valid_multi.refresh_from_db()
        self.assertEqual(valid_single.tariff_capability, "single_rate")
        self.assertEqual(valid_multi.tariff_capability, "multi_rate")

    def test_single_rate_page_has_four_slot_write_preview(self):
        MeterTariffAudit.objects.create(
            meter=self.single,
            initiating_user=self.user,
            capability="single_rate",
            configuration_type="read",
            status="read",
            values_after={"active_rate_count": 1, "prices": ["1.0000"] * 4},
        )
        response = self.client.get(
            reverse("smart_meter:tariff_configure", args=[self.single.pk])
        )
        self.assertContains(response, "Read-only write preview")
        self.assertContains(response, 'class="single-slot-preview"', count=4)

    def test_bulk_selection_requires_review_before_run_starts(self):
        response = self.client.get(reverse("smart_meter:tariff_bulk_setup"))
        setup_token = response.context["submission_token"]
        response = self.client.post(reverse("smart_meter:tariff_bulk_setup"), {
            "price": "40.0000",
            "meter_ids": str(self.single.pk),
            "submission_token": setup_token,
        })
        run = MeterTariffBulkRun.objects.get()
        self.assertEqual(run.status, "draft")
        self.assertRedirects(
            response, reverse("smart_meter:tariff_bulk_confirm", args=[run.pk])
        )

        response = self.client.get(
            reverse("smart_meter:tariff_bulk_confirm", args=[run.pk])
        )
        self.assertContains(response, self.single.meter_number)
        self.assertContains(response, "Single-rate")
        confirm_token = response.context["submission_token"]
        response = self.client.post(
            reverse("smart_meter:tariff_bulk_confirm", args=[run.pk]),
            {"submission_token": confirm_token, "confirm_write": "yes"},
        )
        run.refresh_from_db()
        self.assertEqual(run.status, "running")
        self.assertRedirects(
            response, reverse("smart_meter:tariff_bulk_result", args=[run.pk])
        )

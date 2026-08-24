from django.test import TestCase

from smart_meter.management.commands.meter_listener import (
    _decode_switch_action_from_hex,
)
from smart_meter.models import Meter
from smart_meter.services.command_lifecycle import queue_relay_command
from smart_meter.vendor.switch_OnOff import frame_command


class SwitchFrameTests(TestCase):
    meter_number = "260305510009"

    def test_close_relay_uses_dlt645_1b_action(self):
        frame_hex = frame_command(self.meter_number, 0x1B).hex().upper()

        self.assertIn("4E34", frame_hex)
        self.assertEqual(_decode_switch_action_from_hex(frame_hex), "ON")

    def test_open_relay_uses_dlt645_1a_action(self):
        frame_hex = frame_command(self.meter_number, 0x1A).hex().upper()

        self.assertIn("4D34", frame_hex)
        self.assertEqual(_decode_switch_action_from_hex(frame_hex), "OFF")


class ManualSwitchQueueTests(TestCase):
    def setUp(self):
        self.meter = Meter.objects.create(meter_number="260305510009")

    def test_acknowledged_manual_command_is_not_reused(self):
        first = queue_relay_command(self.meter, "on", source="manual")
        first.status = "acknowledged"
        first.save(update_fields=["status"])

        second = queue_relay_command(self.meter, "on", source="manual")

        self.assertNotEqual(second.pk, first.pk)
        self.assertIn("4E34", second.frame_hex)

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from smart_meter.models import Meter, MeterSettings
from smart_meter.models import MeterRawFrame
from smart_meter.management.commands.meter_listener import _persist_raw_frame
from smart_meter.services.raw_frame_capture import (
    clear_raw_frame_capture_cache,
    effective_raw_frame_capture_mode,
    should_capture_raw_frame,
)


class RawFrameCapturePolicyTests(TestCase):
    def setUp(self):
        self.meter = Meter.objects.create(meter_number="CAPTURE-POLICY-1")
        MeterSettings.objects.update_or_create(
            pk=1,
            defaults={"raw_frame_capture_mode": MeterSettings.RAW_FRAME_CAPTURE_ERRORS},
        )
        clear_raw_frame_capture_cache()

    def tearDown(self):
        clear_raw_frame_capture_cache()

    def test_global_errors_only_skips_normal_and_keeps_error(self):
        self.assertFalse(should_capture_raw_frame(self.meter, is_error=False))
        self.assertTrue(should_capture_raw_frame(self.meter, is_error=True))

    def test_meter_can_override_global_to_all(self):
        self.meter.raw_frame_capture_mode = Meter.RAW_FRAME_CAPTURE_ALL
        self.meter.save(update_fields=["raw_frame_capture_mode"])
        self.assertTrue(should_capture_raw_frame(self.meter, is_error=False))

    def test_expired_all_override_automatically_returns_to_global(self):
        self.meter.raw_frame_capture_mode = Meter.RAW_FRAME_CAPTURE_ALL
        self.meter.raw_frame_capture_until = timezone.now() - timedelta(seconds=1)
        self.meter.save(update_fields=[
            "raw_frame_capture_mode", "raw_frame_capture_until",
        ])
        self.assertEqual(
            effective_raw_frame_capture_mode(self.meter),
            MeterSettings.RAW_FRAME_CAPTURE_ERRORS,
        )
        self.assertFalse(should_capture_raw_frame(self.meter, is_error=False))

    def test_off_override_keeps_no_frames(self):
        self.meter.raw_frame_capture_mode = Meter.RAW_FRAME_CAPTURE_OFF
        self.meter.save(update_fields=["raw_frame_capture_mode"])
        self.assertFalse(should_capture_raw_frame(self.meter, is_error=False))
        self.assertFalse(should_capture_raw_frame(self.meter, is_error=True))

    def test_listener_persistence_gate_skips_normal_and_keeps_error(self):
        common = {
            "meter": self.meter,
            "frame": b"\x68\x01\x02",
            "source_ip": "127.0.0.1",
            "source_port": 12345,
        }
        self.assertIsNone(_persist_raw_frame(**common))
        self.assertEqual(MeterRawFrame.objects.count(), 0)

        stored = _persist_raw_frame(
            **common,
            is_error=True,
            error_reason="test_error",
        )
        self.assertIsNotNone(stored)
        self.assertEqual(MeterRawFrame.objects.count(), 1)
        self.assertEqual(stored.decoded_data["capture_error"], "test_error")

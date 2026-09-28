from datetime import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from properties.models import Property, Unit
from smart_meter.models import Meter, MeterReading
from smart_meter.services.meter_availability import build_meter_availability_timeline


class MeterAvailabilityTimelineTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(
            property_name="H9",
            owner_name="Owner",
            owner_cnic="1111111111111",
            type="apartment",
            property_type="apartment",
            total_units=3,
        )
        self.meters = []
        for index in range(3):
            unit = Unit.objects.create(
                property=self.property,
                unit_number=f"F{index + 1}",
            )
            self.meters.append(
                Meter.objects.create(
                    meter_number=f"H9-M{index + 1}",
                    unit=unit,
                    meter_role=Meter.METER_ROLE_BILLING,
                )
            )

    def _at(self, hour, minute):
        return timezone.make_aware(datetime(2026, 9, 29, hour, minute))

    def _reading(self, meter, hour, minute, value):
        MeterReading.objects.create(
            meter=meter,
            ts=self._at(hour, minute),
            total_energy=Decimal(value),
        )

    def test_each_meter_can_go_offline_at_a_different_time(self):
        # Grid-fed meter: stops first and comes back at 03:00.
        for hour, minute, value in (
            (0, 0, "100.0"), (0, 15, "100.1"), (0, 30, "100.2"),
            (3, 0, "100.3"), (3, 15, "100.4"),
        ):
            self._reading(self.meters[0], hour, minute, value)

        # High-load meter: continues for another 30 minutes.
        for hour, minute, value in (
            (0, 0, "200.0"), (0, 15, "200.1"), (0, 30, "200.2"),
            (0, 45, "200.3"), (1, 0, "200.4"),
            (3, 0, "200.5"), (3, 15, "200.6"),
        ):
            self._reading(self.meters[1], hour, minute, value)

        # Battery-backed meter: runs longer before dropping.
        for hour, minute, value in (
            (0, 0, "300.0"), (0, 15, "300.1"), (0, 30, "300.2"),
            (0, 45, "300.3"), (1, 0, "300.4"), (1, 15, "300.5"),
            (1, 30, "300.6"), (1, 45, "300.7"), (2, 0, "300.8"),
            (3, 0, "300.9"), (3, 15, "301.0"),
        ):
            self._reading(self.meters[2], hour, minute, value)

        result = build_meter_availability_timeline(
            self.meters,
            self._at(0, 0),
            self._at(4, 0),
        )

        rows = {row["meter"].meter_number: row for row in result["rows"]}
        grid_event = rows["H9-M1"]["events"][0]
        high_event = rows["H9-M2"]["events"][0]
        battery_event = rows["H9-M3"]["events"][0]

        self.assertEqual(grid_event["offline_from"], self._at(0, 45))
        self.assertEqual(high_event["offline_from"], self._at(1, 15))
        self.assertEqual(battery_event["offline_from"], self._at(2, 15))

        self.assertEqual(grid_event["online_at"], self._at(3, 0))
        self.assertEqual(high_event["online_at"], self._at(3, 0))
        self.assertEqual(battery_event["online_at"], self._at(3, 0))

    def test_short_normal_snapshot_gap_is_not_marked_offline(self):
        meter = self.meters[0]
        self._reading(meter, 0, 0, "100.0")
        self._reading(meter, 0, 15, "100.1")
        self._reading(meter, 0, 30, "100.2")

        result = build_meter_availability_timeline(
            [meter],
            self._at(0, 0),
            self._at(1, 0),
        )
        self.assertEqual(result["rows"][0]["events"], [])

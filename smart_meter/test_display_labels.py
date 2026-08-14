from datetime import date

from django.test import TestCase

from properties.models import Property, Unit
from smart_meter.models import Meter, MeterInstallation
from smart_meter.utils.display import attach_active_meter_counts


class MeterDisplayLocationTests(TestCase):
    def setUp(self):
        property_obj = Property.objects.create(
            property_name="Meter Label Property",
            owner_name="Test Owner",
            owner_cnic="61101-7777777-7",
            type="Residential",
            property_type="Building",
            total_units=1,
        )
        self.unit = Unit.objects.create(property=property_obj, unit_number="ML-1")

    def test_single_active_installation_displays_unit_number(self):
        meter = Meter.objects.create(
            meter_number="LABEL-1",
            name="Kitchen Meter",
            unit=self.unit,
        )
        MeterInstallation.objects.create(
            meter=meter,
            unit=self.unit,
            start_date=date(2026, 1, 1),
        )

        attach_active_meter_counts([meter])

        self.assertEqual(meter.display_location_name, "ML-1")

    def test_multiple_active_installations_display_each_meter_name(self):
        first = Meter.objects.create(
            meter_number="LABEL-2",
            name="Ground Floor Meter",
            unit=self.unit,
        )
        second = Meter.objects.create(
            meter_number="LABEL-3",
            name="First Floor Meter",
            unit=self.unit,
        )
        for meter in (first, second):
            MeterInstallation.objects.create(
                meter=meter,
                unit=self.unit,
                start_date=date(2026, 1, 1),
            )

        attach_active_meter_counts([first, second])

        self.assertEqual(first.display_location_name, "Ground Floor Meter")
        self.assertEqual(second.display_location_name, "First Floor Meter")

    def test_unnamed_meter_uses_meter_number_on_multi_meter_unit(self):
        named = Meter.objects.create(
            meter_number="LABEL-4",
            name="Named Meter",
            unit=self.unit,
        )
        unnamed = Meter.objects.create(meter_number="LABEL-5", unit=self.unit)
        for meter in (named, unnamed):
            MeterInstallation.objects.create(
                meter=meter,
                unit=self.unit,
                start_date=date(2026, 1, 1),
            )

        attach_active_meter_counts([named, unnamed])

        self.assertEqual(unnamed.display_location_name, "LABEL-5")

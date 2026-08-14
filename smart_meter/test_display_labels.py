from datetime import date

from django.test import TestCase

from properties.models import Property, Unit
from smart_meter.models import Meter, MeterInstallation
from smart_meter.utils.display import attach_active_meter_counts, display_labels_for_units


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

    def test_legacy_active_assignments_without_installations_use_meter_names(self):
        first = Meter.objects.create(
            meter_number="LABEL-6",
            name="Solar Input",
            unit=self.unit,
        )
        second = Meter.objects.create(
            meter_number="LABEL-7",
            name="Grid Input",
            unit=self.unit,
        )

        attach_active_meter_counts([first, second])

        self.assertEqual(first.display_location_name, "Solar Input")
        self.assertEqual(second.display_location_name, "Grid Input")

    def test_inactive_legacy_meter_does_not_force_meter_name(self):
        current = Meter.objects.create(
            meter_number="LABEL-8",
            name="Current Meter",
            unit=self.unit,
        )
        Meter.objects.create(
            meter_number="LABEL-9",
            name="Old Meter",
            unit=self.unit,
            is_active=False,
        )

        attach_active_meter_counts([current])

        self.assertEqual(current.display_location_name, "ML-1")

    def test_monthly_billing_unit_label_lists_active_meter_names(self):
        Meter.objects.create(
            meter_number="LABEL-10",
            name="Shop Front",
            unit=self.unit,
        )
        Meter.objects.create(
            meter_number="LABEL-11",
            name="Shop Rear",
            unit=self.unit,
        )

        labels = display_labels_for_units([self.unit])

        self.assertEqual(labels[self.unit.id], "Shop Front / Shop Rear")

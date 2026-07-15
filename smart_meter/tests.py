from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import TestCase

from leases.models import Lease, LeaseUnitOccupancy
from properties.models import Property, Unit
from smart_meter.models import Meter, MeterInstallation
from smart_meter.services.invoicing import ElectricBillContext
from tenants.models import Tenant


class ElectricBillDescriptionTests(TestCase):
    def test_long_description_keeps_final_total_within_invoice_item_limit(self):
        ctx = ElectricBillContext(
            lease=None,
            meter=SimpleNamespace(meter_number="250619510016-LONG-METER-REFERENCE"),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            beg_kwh=Decimal("123456789.123"),
            end_kwh=Decimal("987654321.987"),
            units=Decimal("864197532.864"),
            unit_rate=Decimal("12345.67"),
            service_charges=Decimal("987654.32"),
        )

        description = ctx.description_text

        self.assertIn(f"total={ctx.line_total}.", description)
        self.assertLessEqual(len(description), 490)


class HistoricalMeterOccupancyTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(
            property_name="Test Property",
            owner_name="Owner",
            owner_cnic="1234512345671",
            type="apartment",
            property_type="apartment",
            total_units=2,
        )
        self.unit_101 = Unit.objects.create(property=self.property, unit_number="101")
        self.unit_202 = Unit.objects.create(property=self.property, unit_number="202")

    def test_unit_allows_multiple_active_meter_installations(self):
        meter_a = Meter.objects.create(meter_number="MTR-1001")
        meter_b = Meter.objects.create(meter_number="MTR-1002")

        MeterInstallation.objects.create(
            meter=meter_a,
            unit=self.unit_101,
            start_date=date(2026, 5, 1),
            start_reading=Decimal("1000.000"),
        )
        MeterInstallation.objects.create(
            meter=meter_b,
            unit=self.unit_101,
            start_date=date(2026, 5, 1),
            start_reading=Decimal("20.000"),
        )

        active_count = self.unit_101.meter_installations.filter(is_active=True).count()
        self.assertEqual(active_count, 2)

    def test_meter_cannot_have_two_active_installations(self):
        meter = Meter.objects.create(meter_number="MTR-2001")
        MeterInstallation.objects.create(
            meter=meter,
            unit=self.unit_101,
            start_date=date(2026, 5, 1),
        )

        second_installation = MeterInstallation(
            meter=meter,
            unit=self.unit_202,
            start_date=date(2026, 5, 10),
        )

        with self.assertRaises(ValidationError):
            second_installation.full_clean()

    def test_lease_allows_only_one_active_occupancy(self):
        tenant = Tenant.objects.create(
            first_name="Test",
            last_name="Tenant",
            cnic="1234512345671",
        )
        lease = Lease.objects.create(
            tenant=tenant,
            unit=self.unit_101,
            start_date=date(2026, 5, 1),
            end_date=date(2027, 4, 30),
            monthly_rent=Decimal("25000.00"),
        )
        LeaseUnitOccupancy.objects.create(
            lease=lease,
            unit=self.unit_101,
            move_in_date=date(2026, 5, 1),
        )

        second_occupancy = LeaseUnitOccupancy(
            lease=lease,
            unit=self.unit_202,
            move_in_date=date(2026, 5, 13),
        )

        with self.assertRaises(ValidationError):
            second_occupancy.full_clean()

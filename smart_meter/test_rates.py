from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from core.models import GlobalSettings
from leases.models import Lease
from properties.models import Property, Unit
from smart_meter.models import Meter
from smart_meter.rates import resolve_electricity_rate
from tenants.models import Tenant


class ElectricityRateResolutionTests(TestCase):
    def setUp(self):
        settings_obj, _ = GlobalSettings.objects.get_or_create(pk=1)
        settings_obj.unit_rate_per_kwh = Decimal("50.0000")
        settings_obj.save(update_fields=["unit_rate_per_kwh"])
        self.property = Property.objects.create(
            property_name="Rate Property",
            owner_name="Rate Owner",
            owner_cnic="61101-1111111-1",
            type="Residential",
            property_type="house",
            total_units=1,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="RATE-1",
        )
        self.meter = Meter.objects.create(
            meter_number="RATE0000000000001",
            unit=self.unit,
            unit_rate=None,
        )
        tenant = Tenant.objects.create(
            first_name="Rate",
            last_name="Tenant",
            cnic="61101-2222222-2",
        )
        self.lease = Lease.objects.create(
            tenant=tenant,
            unit=self.unit,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=365),
            monthly_rent=Decimal("10000.00"),
            electric_unit_rate=None,
        )

    def test_last_nonblank_level_wins(self):
        resolution = resolve_electricity_rate(lease=self.lease, meter=self.meter)
        self.assertEqual(
            (resolution.rate, resolution.source), (Decimal("50.0000"), "Global")
        )

        self.property.electricity_unit_rate = Decimal("51.0000")
        self.property.save(update_fields=["electricity_unit_rate"])
        self.assertEqual(
            resolve_electricity_rate(lease=self.lease, meter=self.meter).source,
            "Property",
        )

        self.unit.electricity_unit_rate = Decimal("52.0000")
        self.unit.save(update_fields=["electricity_unit_rate"])
        self.meter.unit_rate = Decimal("53.0000")
        self.meter.save(update_fields=["unit_rate"])
        self.lease.electric_unit_rate = Decimal("54.0000")
        self.lease.save(update_fields=["electric_unit_rate"])
        resolution = resolve_electricity_rate(lease=self.lease, meter=self.meter)
        self.assertEqual(
            (resolution.rate, resolution.source), (Decimal("54.0000"), "Lease")
        )

    def test_zero_is_an_explicit_override(self):
        self.lease.electric_unit_rate = Decimal("0.0000")
        self.lease.save(update_fields=["electric_unit_rate"])
        resolution = resolve_electricity_rate(lease=self.lease, meter=self.meter)
        self.assertEqual(
            (resolution.rate, resolution.source), (Decimal("0.0000"), "Lease")
        )

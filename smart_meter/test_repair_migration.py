import importlib
from datetime import date, datetime
from decimal import Decimal

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from leases.models import Lease, LeaseUnitOccupancy
from properties.models import Property, Unit
from smart_meter.models import Meter, MeterInstallation, MeterReading
from tenants.models import Tenant


repair_migration = importlib.import_module(
    "smart_meter.migrations.0017_repair_f54_f56_billing_links"
)


class F54F56BillingRepairMigrationTests(TestCase):
    def test_forward_and_reverse_preserve_the_flat_1_tenant_boundary(self):
        property_obj = Property.objects.create(
            property_name="F54",
            owner_name="Owner",
            owner_cnic="42101-0000000-1",
            type="residential",
            property_type="apartment",
            total_units=1,
        )
        unit = Unit.objects.create(
            property=property_obj,
            unit_number="F54-FLAT# 01",
            is_smart_meter=True,
        )
        prior_tenant = Tenant.objects.create(
            first_name="Prior",
            last_name="Tenant",
            cnic="42101-0000000-2",
        )
        new_tenant = Tenant.objects.create(
            first_name="New",
            last_name="Tenant",
            cnic="42101-0000000-3",
        )
        prior_lease = Lease.objects.create(
            tenant=prior_tenant,
            unit=unit,
            start_date=date(2026, 5, 31),
            end_date=date(2026, 6, 30),
            monthly_rent=Decimal("25000"),
            electricity_bill_by_owner=False,
        )
        new_lease = Lease.objects.create(
            tenant=new_tenant,
            unit=unit,
            start_date=date(2026, 7, 1),
            end_date=date(2027, 6, 30),
            monthly_rent=Decimal("25000"),
            electricity_bill_by_owner=False,
        )
        LeaseUnitOccupancy.objects.create(
            lease=prior_lease,
            unit=unit,
            move_in_date=date(2026, 5, 31),
            move_out_date=date(2026, 6, 30),
        )
        meter = Meter.objects.create(
            unit=unit,
            meter_number="241203510003",
            meter_type="electric",
            billing_mode="postpaid",
        )
        old_installation = MeterInstallation.objects.create(
            meter=meter,
            unit=unit,
            lease=prior_lease,
            start_date=date(2025, 9, 27),
            start_reading=Decimal("100"),
            reason="backfill",
        )
        for reading_date, value in (
            (date(2026, 6, 30), Decimal("200")),
            (date(2026, 7, 1), Decimal("205")),
        ):
            MeterReading.objects.create(
                meter=meter,
                ts=timezone.make_aware(datetime.combine(reading_date, datetime.min.time())),
                total_energy=value,
            )

        repair_migration.repair_billing_links(apps, None)

        new_lease.refresh_from_db()
        old_installation.refresh_from_db()
        self.assertTrue(new_lease.electricity_bill_by_owner)
        self.assertEqual(old_installation.end_date, date(2026, 6, 30))
        self.assertFalse(old_installation.is_active)
        new_installation = MeterInstallation.objects.get(
            meter=meter,
            lease=new_lease,
            start_date=date(2026, 7, 1),
        )
        self.assertTrue(new_installation.is_active)
        self.assertEqual(new_installation.start_reading, Decimal("205"))
        self.assertTrue(
            LeaseUnitOccupancy.objects.filter(
                lease=new_lease,
                unit=unit,
                move_in_date=date(2026, 7, 1),
            ).exists()
        )

        repair_migration.reverse_repair(apps, None)

        new_lease.refresh_from_db()
        old_installation.refresh_from_db()
        self.assertFalse(new_lease.electricity_bill_by_owner)
        self.assertIsNone(old_installation.end_date)
        self.assertTrue(old_installation.is_active)
        self.assertFalse(
            MeterInstallation.objects.filter(
                reason=repair_migration.REPAIR_REASON
            ).exists()
        )

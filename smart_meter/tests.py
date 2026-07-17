from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from leases.models import Lease, LeaseUnitOccupancy
from properties.models import Property, Unit
from smart_meter.models import Meter, MeterInstallation, MeterRoleHistory
from smart_meter.services.invoicing import ElectricBillContext
from tenants.models import Tenant


class MeterFormUnitOrderingTests(TestCase):
    def setUp(self):
        alpha = Property.objects.create(
            property_name="Alpha Property",
            owner_name="Owner Alpha",
            owner_cnic="1234511111111",
            type="apartment",
            property_type="apartment",
            total_units=2,
        )
        beta = Property.objects.create(
            property_name="Beta Property",
            owner_name="Owner Beta",
            owner_cnic="1234522222222",
            type="apartment",
            property_type="apartment",
            total_units=1,
        )
        self.alpha_1 = Unit.objects.create(property=alpha, unit_number="1")
        self.alpha_2 = Unit.objects.create(property=alpha, unit_number="2")
        self.beta_1 = Unit.objects.create(property=beta, unit_number="1")

    def test_add_and_convert_forms_order_units_by_property_then_unit(self):
        from smart_meter.forms import MeterForm, UnknownToMeterForm

        expected_ids = [self.alpha_1.pk, self.alpha_2.pk, self.beta_1.pk]
        for form_class in (MeterForm, UnknownToMeterForm):
            field = form_class().fields["unit"]
            self.assertEqual(list(field.queryset.values_list("pk", flat=True)), expected_ids)
            self.assertEqual(
                field.label_from_instance(self.alpha_1),
                "Alpha Property / Unit 1",
            )


class MeterRoleUpdateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="meter-admin", password="test-pass")
        self.user.user_permissions.add(Permission.objects.get(codename="change_meter"))
        self.meter = Meter.objects.create(meter_number="ROLE-UPDATE-1")
        MeterRoleHistory.objects.create(
            meter=self.meter,
            role=Meter.METER_ROLE_BILLING,
            start_date=date(2026, 1, 1),
        )
        self.client.force_login(self.user)

    def test_inline_role_update_returns_saved_role_and_label(self):
        response = self.client.post(
            reverse("smart_meter:meter_role_update", args=[self.meter.pk]),
            {"meter_role": Meter.METER_ROLE_CHECK},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], Meter.METER_ROLE_CHECK)
        self.assertEqual(response.json()["label"], "Check / Audit")
        self.meter.refresh_from_db()
        self.assertEqual(self.meter.meter_role, Meter.METER_ROLE_CHECK)
        self.assertEqual(self.meter.role_history.filter(is_active=True, end_date__isnull=True).count(), 1)


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

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from properties.models import Property, Unit
from smart_meter.forms import MeterCheckGroupMembershipForm
from smart_meter.models import Meter, MeterCheckGroup, MeterCheckGroupMembership


class CheckGroupTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="check-group-admin",
            password="test-pass",
            email="check-groups@example.com",
        )
        self.client.force_login(self.user)
        self.property_a = self._property("Coverage A")
        self.property_b = self._property("Coverage B")
        self.unit_a = Unit.objects.create(property=self.property_a, unit_number="A-1")
        self.unit_b = Unit.objects.create(property=self.property_b, unit_number="B-1")

    def _property(self, name):
        return Property.objects.create(
            property_name=name,
            owner_name="Owner",
            owner_cnic=f"{Property.objects.count() + 1:013d}",
            type="apartment",
            property_type="apartment",
            total_units=2,
        )

    def _meter(self, number, unit, *, role=Meter.METER_ROLE_BILLING, active=True, name=""):
        return Meter.objects.create(
            meter_number=number,
            name=name,
            unit=unit,
            meter_role=role,
            is_active=active,
        )

    def _meter_post(self, meter, *, role, name=None, replacement=None):
        data = {
            "unit": meter.unit_id,
            "meter_number": meter.meter_number,
            "name": meter.name if name is None else name,
            "meter_type": Meter.METER_TYPE_ELECTRIC,
            "billing_mode": "postpaid",
            "meter_role": role,
            "power_status": "on",
            "unit_rate": "50.00",
            "service_charges": "250.00",
            "min_balance_alert": "100.00",
            "min_balance_cutoff": "0.00",
            "is_active": "on",
            "installed_at": timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
            "notes": "Role transition test",
        }
        if replacement:
            data["replacement_check_meter"] = replacement.pk
        return data


class CheckGroupMembershipTests(CheckGroupTestCase):
    def test_assignment_choices_show_only_active_unassigned_billing_meters(self):
        available = self._meter("ACTIVE-AVAILABLE", self.unit_a)
        inactive = self._meter("INACTIVE", self.unit_a, active=False)
        assigned = self._meter("ACTIVE-ASSIGNED", self.unit_b)
        audit = self._meter("AUDIT-1", self.unit_a, role=Meter.METER_ROLE_CHECK)
        other_audit = self._meter("AUDIT-2", self.unit_b, role=Meter.METER_ROLE_CHECK)
        group = MeterCheckGroup.objects.create(name="Group A", check_meter=audit)
        other_group = MeterCheckGroup.objects.create(name="Group B", check_meter=other_audit)
        MeterCheckGroupMembership.objects.create(
            group=other_group,
            billing_meter=assigned,
            start_date=date(2026, 1, 1),
        )

        form = MeterCheckGroupMembershipForm(group=group)
        meter_ids = set(form.fields["billing_meter"].queryset.values_list("pk", flat=True))

        self.assertIn(available.pk, meter_ids)
        self.assertNotIn(inactive.pk, meter_ids)
        self.assertNotIn(assigned.pk, meter_ids)

    def test_group_accepts_selected_billing_meters_from_different_properties(self):
        audit = self._meter("AUDIT-CROSS", self.unit_a, role=Meter.METER_ROLE_CHECK)
        group = MeterCheckGroup.objects.create(name="Cross-property", check_meter=audit)
        meter_a = self._meter("CROSS-A", self.unit_a)
        meter_b = self._meter("CROSS-B", self.unit_b)

        MeterCheckGroupMembership.objects.create(
            group=group, billing_meter=meter_a, start_date=date(2026, 1, 1)
        )
        MeterCheckGroupMembership.objects.create(
            group=group, billing_meter=meter_b, start_date=date(2026, 1, 1)
        )

        self.assertEqual(
            set(group.active_billing_meters().values_list("pk", flat=True)),
            {meter_a.pk, meter_b.pk},
        )

    def test_duplicate_overlapping_membership_in_same_group_is_rejected(self):
        audit = self._meter("AUDIT-DUP", self.unit_a, role=Meter.METER_ROLE_CHECK)
        billing = self._meter("BILL-DUP", self.unit_a)
        group = MeterCheckGroup.objects.create(name="Duplicate guard", check_meter=audit)
        MeterCheckGroupMembership.objects.create(
            group=group, billing_meter=billing, start_date=date(2026, 1, 1)
        )

        duplicate = MeterCheckGroupMembership(
            group=group, billing_meter=billing, start_date=date(2026, 2, 1)
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_reconciliation_uses_each_membership_effective_date_window(self):
        audit = self._meter("AUDIT-DATES", self.unit_a, role=Meter.METER_ROLE_CHECK)
        first = self._meter("DATES-A", self.unit_a)
        second = self._meter("DATES-B", self.unit_b)
        group = MeterCheckGroup.objects.create(name="Dated coverage", check_meter=audit)
        MeterCheckGroupMembership.objects.create(
            group=group,
            billing_meter=first,
            start_date=date(2026, 8, 5),
            end_date=date(2026, 8, 10),
            is_active=False,
        )
        MeterCheckGroupMembership.objects.create(
            group=group,
            billing_meter=second,
            start_date=date(2026, 8, 15),
        )
        calls = []

        def fake_series(meters, start_date, end_date, granularity):
            calls.append((tuple(meters.values_list("pk", flat=True)), start_date, end_date, granularity))
            return [], [], [], {"total_kwh": Decimal("0")}

        with patch("smart_meter.views_dashboard._per_meter_series", side_effect=fake_series):
            response = self.client.get(
                reverse("smart_meter:meter_check_group_detail", args=[group.pk]),
                {"start": "2026-08-01", "end": "2026-08-20"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(((audit.pk,), date(2026, 8, 1), date(2026, 8, 20), "daily"), calls)
        self.assertIn(((first.pk,), date(2026, 8, 5), date(2026, 8, 10), "daily"), calls)
        self.assertIn(((second.pk,), date(2026, 8, 15), date(2026, 8, 20), "daily"), calls)


class MeterRoleTransitionTests(CheckGroupTestCase):
    def test_audit_to_billing_requires_replacement_without_partial_save(self):
        audit = self._meter("ROLE-AUDIT", self.unit_a, role=Meter.METER_ROLE_CHECK, name="Original")
        MeterCheckGroup.objects.create(name="Role group", check_meter=audit)

        response = self.client.post(
            reverse("smart_meter:meter_edit", args=[audit.pk]),
            self._meter_post(audit, role=Meter.METER_ROLE_BILLING, name="Must not save"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "replacement_check_meter",
            "Select another active Audit meter to take over this meter's Check Group.",
        )
        audit.refresh_from_db()
        self.assertEqual(audit.meter_role, Meter.METER_ROLE_CHECK)
        self.assertEqual(audit.name, "Original")

    def test_audit_to_billing_reassigns_group_and_updates_role(self):
        audit = self._meter("ROLE-OLD", self.unit_a, role=Meter.METER_ROLE_CHECK)
        replacement = self._meter("ROLE-NEW", self.unit_b, role=Meter.METER_ROLE_CHECK)
        group = MeterCheckGroup.objects.create(name="Replacement group", check_meter=audit)

        response = self.client.post(
            reverse("smart_meter:meter_edit", args=[audit.pk]),
            self._meter_post(
                audit,
                role=Meter.METER_ROLE_BILLING,
                replacement=replacement,
            ),
        )

        self.assertEqual(response.status_code, 302)
        audit.refresh_from_db()
        group.refresh_from_db()
        self.assertEqual(audit.meter_role, Meter.METER_ROLE_BILLING)
        self.assertEqual(group.check_meter, replacement)

    def test_billing_to_audit_ends_membership_and_updates_role(self):
        audit = self._meter("ROLE-GROUP-AUDIT", self.unit_a, role=Meter.METER_ROLE_CHECK)
        billing = self._meter("ROLE-BILLING", self.unit_b)
        group = MeterCheckGroup.objects.create(name="Close membership", check_meter=audit)
        membership = MeterCheckGroupMembership.objects.create(
            group=group,
            billing_meter=billing,
            start_date=date(2026, 1, 1),
        )

        response = self.client.post(
            reverse("smart_meter:meter_edit", args=[billing.pk]),
            self._meter_post(billing, role=Meter.METER_ROLE_CHECK),
        )

        self.assertEqual(response.status_code, 302)
        billing.refresh_from_db()
        membership.refresh_from_db()
        self.assertEqual(billing.meter_role, Meter.METER_ROLE_CHECK)
        self.assertFalse(membership.is_active)
        self.assertIsNotNone(membership.end_date)

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from properties.models import Property, Unit
from smart_meter.forms import MeterCheckGroupMembershipForm
from smart_meter.models import (
    Meter,
    MeterCheckGroup,
    MeterCheckGroupMembership,
    MeterReading,
)


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
    def test_hard_delete_workflow_reassigns_all_memberships_before_deleting(self):
        source_audit = self._meter(
            "AUDIT-DELETE-SOURCE", self.unit_a, role=Meter.METER_ROLE_CHECK
        )
        target_audit = self._meter(
            "AUDIT-DELETE-TARGET", self.unit_b, role=Meter.METER_ROLE_CHECK
        )
        source = MeterCheckGroup.objects.create(
            name="Delete source", check_meter=source_audit
        )
        target = MeterCheckGroup.objects.create(
            name="Delete target", check_meter=target_audit
        )
        active_meter = self._meter("DELETE-ACTIVE", self.unit_a)
        ended_meter = self._meter("DELETE-ENDED", self.unit_b)
        active_membership = MeterCheckGroupMembership.objects.create(
            group=source,
            billing_meter=active_meter,
            start_date=date(2026, 1, 1),
        )
        ended_membership = MeterCheckGroupMembership.objects.create(
            group=source,
            billing_meter=ended_meter,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            is_active=False,
        )
        manage_url = reverse(
            "smart_meter:meter_check_group_delete_manage", args=[source.pk]
        )

        info_response = self.client.get(
            manage_url, HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(info_response.status_code, 200)
        self.assertEqual(info_response.json()["membership_count"], 2)
        self.assertEqual(
            [item["id"] for item in info_response.json()["targets"]], [target.pk]
        )

        blocked_response = self.client.post(
            manage_url,
            {"action": "delete"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(blocked_response.status_code, 409)
        self.assertTrue(MeterCheckGroup.objects.filter(pk=source.pk).exists())

        individual_response = self.client.post(
            manage_url,
            {
                "action": "reassign",
                "membership_id": active_membership.pk,
                "target_group_id": target.pk,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(individual_response.status_code, 200)
        self.assertEqual(individual_response.json()["membership_count"], 1)

        reassign_response = self.client.post(
            manage_url,
            {"action": "reassign_all", "target_group_id": target.pk},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(reassign_response.status_code, 200)
        self.assertEqual(reassign_response.json()["membership_count"], 0)
        active_membership.refresh_from_db()
        ended_membership.refresh_from_db()
        self.assertEqual(active_membership.group_id, target.pk)
        self.assertEqual(ended_membership.group_id, target.pk)

        delete_response = self.client.post(
            manage_url,
            {"action": "delete"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["deleted"])
        self.assertFalse(MeterCheckGroup.objects.filter(pk=source.pk).exists())
        self.assertTrue(Meter.objects.filter(pk=source_audit.pk).exists())
        self.assertEqual(
            MeterCheckGroupMembership.objects.filter(group=target).count(), 2
        )

    def test_hard_delete_list_shows_ajax_management_controls(self):
        audit = self._meter("AUDIT-DELETE-LIST", self.unit_a, role=Meter.METER_ROLE_CHECK)
        group = MeterCheckGroup.objects.create(name="Delete list", check_meter=audit)
        assigned_audit = self._meter(
            "AUDIT-DELETE-USED", self.unit_b, role=Meter.METER_ROLE_CHECK
        )
        assigned_group = MeterCheckGroup.objects.create(
            name="Delete assigned", check_meter=assigned_audit
        )
        MeterCheckGroupMembership.objects.create(
            group=assigned_group,
            billing_meter=self._meter("BILL-DELETE-USED", self.unit_b),
            start_date=date(2026, 8, 1),
        )

        response = self.client.get(reverse("smart_meter:meter_check_group_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="Delete Check Group"')
        self.assertNotContains(response, "Hard Delete")
        self.assertContains(
            response,
            reverse("smart_meter:meter_check_group_delete_manage", args=[group.pk]),
        )
        self.assertContains(response, "checkGroupBulkApply")
        self.assertContains(response, 'data-membership-count="0"')
        self.assertContains(response, 'data-membership-count="1"')
        self.assertContains(response, "await deleteEmptyGroup(button)")
        self.assertContains(response, "data-group-name-display")
        self.assertContains(
            response,
            reverse("smart_meter:meter_check_group_name_update", args=[group.pk]),
        )

    def test_check_group_name_can_be_updated_inline(self):
        audit = self._meter("AUDIT-NAME", self.unit_a, role=Meter.METER_ROLE_CHECK)
        group = MeterCheckGroup.objects.create(name="Old group name", check_meter=audit)
        update_url = reverse(
            "smart_meter:meter_check_group_name_update", args=[group.pk]
        )

        response = self.client.post(
            update_url,
            {"name": "  Updated group name  "},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Updated group name")
        group.refresh_from_db()
        self.assertEqual(group.name, "Updated group name")

        invalid_response = self.client.post(
            update_url,
            {"name": "   "},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(invalid_response.status_code, 400)
        group.refresh_from_db()
        self.assertEqual(group.name, "Updated group name")

    def test_membership_can_be_updated_and_deleted_with_ajax(self):
        audit = self._meter("AUDIT-MANAGE", self.unit_a, role=Meter.METER_ROLE_CHECK)
        billing = self._meter("BILL-MANAGE", self.unit_b)
        group = MeterCheckGroup.objects.create(name="Manage membership", check_meter=audit)
        membership = MeterCheckGroupMembership.objects.create(
            group=group,
            billing_meter=billing,
            start_date=date(2026, 8, 1),
        )
        manage_url = reverse(
            "smart_meter:meter_check_group_membership_manage",
            args=[group.pk, membership.pk],
        )

        update_response = self.client.post(
            manage_url,
            {
                "action": "update",
                "start_date": "2026-08-02",
                "end_date": "2026-08-20",
                "notes": "Updated through the detail screen.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(update_response.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.start_date, date(2026, 8, 2))
        self.assertEqual(membership.end_date, date(2026, 8, 20))
        self.assertFalse(membership.is_active)
        self.assertEqual(membership.notes, "Updated through the detail screen.")

        delete_response = self.client.post(
            manage_url,
            {"action": "delete"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["deleted"])
        self.assertFalse(MeterCheckGroupMembership.objects.filter(pk=membership.pk).exists())

    def test_group_detail_shows_membership_actions_and_detail_layout(self):
        audit = self._meter("AUDIT-LAYOUT", self.unit_a, role=Meter.METER_ROLE_CHECK)
        billing = self._meter("BILL-LAYOUT", self.unit_b)
        group = MeterCheckGroup.objects.create(name="Layout", check_meter=audit)
        membership = MeterCheckGroupMembership.objects.create(
            group=group,
            billing_meter=billing,
            start_date=date(2026, 8, 1),
        )

        response = self.client.get(
            reverse("smart_meter:meter_check_group_detail", args=[group.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "max-width: 1720px")
        self.assertContains(response, "data-membership-edit")
        self.assertContains(response, "data-membership-delete")
        self.assertContains(
            response,
            reverse(
                "smart_meter:meter_check_group_membership_manage",
                args=[group.pk, membership.pk],
            ),
        )
        self.assertContains(response, "ld-detail-layout")
        self.assertContains(response, "showBillingView('summary')")

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

    def test_reconciliation_quick_ranges_use_closed_previous_periods(self):
        audit = self._meter("AUDIT-RANGES", self.unit_a, role=Meter.METER_ROLE_CHECK)
        group = MeterCheckGroup.objects.create(name="Quick ranges", check_meter=audit)
        expected_ranges = {
            "this_month": (date(2026, 8, 1), date(2026, 8, 26)),
            "last_month": (date(2026, 7, 1), date(2026, 7, 31)),
            "this_week": (date(2026, 8, 24), date(2026, 8, 26)),
            "last_week": (date(2026, 8, 17), date(2026, 8, 23)),
        }
        empty_series = ([], [], [], {"total_kwh": Decimal("0")})

        with (
            patch("smart_meter.views.timezone.localdate", return_value=date(2026, 8, 26)),
            patch("smart_meter.views_dashboard._per_meter_series", return_value=empty_series),
        ):
            for range_key, expected in expected_ranges.items():
                with self.subTest(range_key=range_key):
                    response = self.client.get(
                        reverse("smart_meter:meter_check_group_detail", args=[group.pk]),
                        {"range": range_key},
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.context["start_date"], expected[0])
                    self.assertEqual(response.context["end_date"], expected[1])
                    self.assertEqual(response.context["quick_range"], range_key)

        self.assertContains(response, "checkGroupDateFilter")
        self.assertContains(response, "billingSummaryButton")

    def test_reconciliation_reports_end_date_reading_freshness_and_member_order(self):
        audit = self._meter("AUDIT-FRESH", self.unit_a, role=Meter.METER_ROLE_CHECK)
        meter_b = self._meter("FRESH-B", self.unit_b)
        meter_a = self._meter("FRESH-A", self.unit_a)
        group = MeterCheckGroup.objects.create(name="Freshness", check_meter=audit)
        for meter in (meter_b, meter_a):
            MeterCheckGroupMembership.objects.create(
                group=group,
                billing_meter=meter,
                start_date=date(2026, 8, 1),
            )
        MeterReading.objects.create(
            meter=audit,
            ts=timezone.make_aware(datetime(2026, 8, 19, 23, 0)),
            total_energy=Decimal("10"),
        )
        MeterReading.objects.create(
            meter=audit,
            ts=timezone.make_aware(datetime(2026, 8, 21, 0, 15)),
            total_energy=Decimal("11"),
        )
        MeterReading.objects.create(
            meter=meter_a,
            ts=timezone.make_aware(datetime(2026, 8, 20, 12, 0)),
            total_energy=Decimal("20"),
        )
        MeterReading.objects.create(
            meter=meter_a,
            ts=timezone.make_aware(datetime(2026, 8, 21, 0, 5)),
            total_energy=Decimal("21"),
        )

        empty_totals = {"total_kwh": Decimal("0")}
        with patch(
            "smart_meter.views_dashboard._per_meter_series",
            return_value=([], [], [], empty_totals),
        ):
            response = self.client.get(
                reverse("smart_meter:meter_check_group_detail", args=[group.pk]),
                {"start": "2026-08-01", "end": "2026-08-20"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["audit_reading_status"], "15m after")
        self.assertEqual(
            timezone.localtime(response.context["audit_last_reading_at"]),
            timezone.make_aware(datetime(2026, 8, 21, 0, 15)),
        )
        self.assertEqual(response.context["audit_last_total_energy"], Decimal("11"))
        memberships = response.context["memberships"]
        self.assertEqual(
            [membership.billing_meter_id for membership in memberships],
            [meter_a.pk, meter_b.pk],
        )
        self.assertEqual(
            memberships[0].billing_meter.report_reading_status,
            "10m before",
        )
        self.assertEqual(
            memberships[0].billing_meter.report_last_reading_date,
            date(2026, 8, 21),
        )
        self.assertEqual(
            memberships[1].billing_meter.report_reading_status,
            "No reading",
        )
        chart_data = response.context["billing_chart_data"]
        self.assertEqual(chart_data["auditLastReading"]["totalEnergy"], 11.0)
        self.assertEqual(chart_data["datasets"][0]["serial"], 1)
        self.assertEqual(chart_data["datasets"][0]["meterId"], meter_a.pk)
        self.assertEqual(chart_data["datasets"][0]["meterNumber"], meter_a.meter_number)
        self.assertTrue(chart_data["datasets"][0]["unitNumber"])
        self.assertTrue(chart_data["datasets"][0]["unitLabel"].startswith("Coverage /"))
        self.assertEqual(list(response.context["audit_group_options"]), [group])
        self.assertContains(response, "auditSummaryButton")
        self.assertContains(response, "auditDetailButton")
        self.assertContains(response, "data-audit-group-nav")
        self.assertContains(response, "billingTableButton")
        self.assertContains(response, "billingJpgButton")
        self.assertNotContains(response, "billingGraphButton")
        self.assertContains(response, "tooltip: {enabled: false}")


class MeterRoleTransitionTests(CheckGroupTestCase):
    def test_audit_to_billing_requires_replacement_without_partial_save(self):
        audit = self._meter("ROLE-AUDIT", self.unit_a, role=Meter.METER_ROLE_CHECK, name="Original")
        dependent = self._meter("ROLE-DEPENDENT", self.unit_b)
        group = MeterCheckGroup.objects.create(name="Role group", check_meter=audit)
        MeterCheckGroupMembership.objects.create(
            group=group,
            billing_meter=dependent,
            start_date=date(2026, 1, 1),
        )

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
        dependent = self._meter("ROLE-DEPENDENT-2", self.unit_b)
        group = MeterCheckGroup.objects.create(name="Replacement group", check_meter=audit)
        MeterCheckGroupMembership.objects.create(
            group=group,
            billing_meter=dependent,
            start_date=date(2026, 1, 1),
        )

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

    def test_audit_to_billing_deactivates_group_without_active_members(self):
        audit = self._meter("ROLE-EMPTY", self.unit_a, role=Meter.METER_ROLE_CHECK)
        group = MeterCheckGroup.objects.create(name="Empty group", check_meter=audit)

        response = self.client.post(
            reverse("smart_meter:meter_edit", args=[audit.pk]),
            self._meter_post(audit, role=Meter.METER_ROLE_BILLING),
        )

        self.assertEqual(response.status_code, 302)
        audit.refresh_from_db()
        group.refresh_from_db()
        self.assertEqual(audit.meter_role, Meter.METER_ROLE_BILLING)
        self.assertFalse(group.is_active)

    def test_inline_audit_to_billing_deactivates_empty_group(self):
        audit = self._meter("ROLE-INLINE-EMPTY", self.unit_a, role=Meter.METER_ROLE_CHECK)
        group = MeterCheckGroup.objects.create(name="Inline empty group", check_meter=audit)

        response = self.client.post(
            reverse("smart_meter:meter_role_update", args=[audit.pk]),
            {"meter_role": Meter.METER_ROLE_BILLING},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        audit.refresh_from_db()
        group.refresh_from_db()
        self.assertEqual(audit.meter_role, Meter.METER_ROLE_BILLING)
        self.assertFalse(group.is_active)

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

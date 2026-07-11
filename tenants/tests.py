from django.test import TestCase

# Create your tests here.

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from leases.services.lease_expiry import (
    attach_lease_expiry_countdown,
    get_lease_expiry_countdown,
)


class LeaseExpiryCountdownTests(SimpleTestCase):
    def make_lease(self, *, days=30, status="active", has_end_date=True):
        end_date = timezone.localdate() + timedelta(days=days) if has_end_date else None
        return SimpleNamespace(status=status, end_date=end_date)

    def test_exactly_60_days_displays(self):
        result = get_lease_expiry_countdown(self.make_lease(days=60))
        self.assertIsNotNone(result)
        self.assertEqual(result.label, "60 days left")

    def test_fewer_than_60_days_displays_correct_value(self):
        result = get_lease_expiry_countdown(self.make_lease(days=45))
        self.assertEqual(result.days_left, 45)
        self.assertEqual(result.label, "45 days left")

    def test_one_day_uses_singular_wording(self):
        result = get_lease_expiry_countdown(self.make_lease(days=1))
        self.assertEqual(result.label, "1 day left")

    def test_more_than_60_days_is_hidden(self):
        self.assertIsNone(get_lease_expiry_countdown(self.make_lease(days=61)))

    def test_missing_end_date_is_hidden(self):
        self.assertIsNone(
            get_lease_expiry_countdown(self.make_lease(has_end_date=False))
        )

    def test_inactive_lease_is_hidden(self):
        self.assertIsNone(
            get_lease_expiry_countdown(self.make_lease(days=30, status="inactive"))
        )

    def test_ended_lease_is_hidden(self):
        self.assertIsNone(get_lease_expiry_countdown(self.make_lease(days=-1)))

    def test_attached_value_uses_same_shared_calculation(self):
        lease = self.make_lease(days=20)
        shared_result = get_lease_expiry_countdown(lease)
        attach_lease_expiry_countdown(lease)
        self.assertEqual(lease.expiry_days_left, shared_result.days_left)
        self.assertEqual(lease.expiry_countdown_label, shared_result.label)


class TenantListExpiryMarkupTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template_source = (
            Path(__file__).resolve().parent
            / "templates"
            / "tenants"
            / "tenant_list.html"
        ).read_text(encoding="utf-8")

    def test_desktop_countdown_is_below_end_date(self):
        desktop_end = self.template_source.index(
            'End: <span class="{% if lease.expiry_countdown_label %}'
        )
        desktop_countdown = self.template_source.index(
            'lease-expiry-countdown lease-expiry-countdown-desktop', desktop_end
        )
        desktop_block_end = self.template_source.index("</div>", desktop_countdown)
        self.assertLess(desktop_end, desktop_countdown)
        self.assertLess(desktop_countdown, desktop_block_end)

    def test_mobile_countdown_is_between_start_and_end(self):
        mobile_start = self.template_source.index('class="tcv2-date-start"')
        mobile_countdown = self.template_source.index(
            'lease-expiry-countdown lease-expiry-countdown-mobile', mobile_start
        )
        mobile_end = self.template_source.index('class="tcv2-date-end"', mobile_countdown)
        self.assertLess(mobile_start, mobile_countdown)
        self.assertLess(mobile_countdown, mobile_end)

    def test_reduced_motion_disables_animation(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.template_source)
        reduced_motion_start = self.template_source.index(
            "@media (prefers-reduced-motion: reduce)"
        )
        reduced_motion_block = self.template_source[
            reduced_motion_start : reduced_motion_start + 220
        ]
        self.assertIn("animation:none !important", reduced_motion_block)

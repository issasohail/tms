from unittest.mock import patch

from django.test import TestCase

from punjab_estamp.models import PunjabEStampDistrict, PunjabEStampTehsil
from punjab_estamp.services.sync import (
    TehsilOption,
    _options_from_json,
    sync_district_tehsils,
)


class PunjabEStampSyncTests(TestCase):
    def setUp(self):
        self.district = PunjabEStampDistrict.objects.create(
            name="Phase 7 Test District", portal_value="phase7-test", sort_order=999
        )

    def test_json_parser_accepts_common_kendo_shapes(self):
        options = _options_from_json(
            {
                "Data": [
                    {"Value": "101", "Text": "Lahore City"},
                    {"TehsilID": 102, "TehsilName": "Model Town"},
                ]
            }
        )
        self.assertEqual(
            [(row.portal_value, row.name) for row in options],
            [("101", "Lahore City"), ("102", "Model Town")],
        )

    @patch("punjab_estamp.services.sync.fetch_tehsil_options")
    def test_sync_upserts_verified_live_options(self, fetch_mock):
        PunjabEStampTehsil.objects.create(
            district=self.district,
            name="Old Name",
            portal_value="101",
            active=False,
            sort_order=50,
        )
        fetch_mock.return_value = [
            TehsilOption("Lahore City", "101"),
            TehsilOption("Model Town", "102"),
        ]

        report = sync_district_tehsils(self.district)

        self.assertEqual(report["found"], 2)
        self.assertEqual(report["created"], 1)
        self.assertEqual(report["updated"], 1)
        self.assertEqual(
            list(
                PunjabEStampTehsil.objects.filter(district=self.district)
                .order_by("sort_order")
                .values_list("portal_value", "name", "active")
            ),
            [("101", "Lahore City", True), ("102", "Model Town", True)],
        )
        self.district.refresh_from_db()
        self.assertIsNotNone(self.district.last_synced_at)

    @patch("punjab_estamp.services.sync.fetch_tehsil_options")
    def test_sync_dry_run_does_not_change_database(self, fetch_mock):
        fetch_mock.return_value = [TehsilOption("Lahore City", "101")]

        report = sync_district_tehsils(self.district, dry_run=True)

        self.assertTrue(report["dry_run"])
        self.assertEqual(report["created"], 1)
        self.assertFalse(PunjabEStampTehsil.objects.filter(district=self.district).exists())
        self.district.refresh_from_db()
        self.assertIsNone(self.district.last_synced_at)

    @patch("punjab_estamp.services.sync.fetch_tehsil_options")
    def test_sync_does_not_deactivate_missing_cached_tehsil_by_default(self, fetch_mock):
        old = PunjabEStampTehsil.objects.create(
            district=self.district, name="Legacy Verified", portal_value="999", active=True
        )
        fetch_mock.return_value = [TehsilOption("Lahore City", "101")]

        sync_district_tehsils(self.district)

        old.refresh_from_db()
        self.assertTrue(old.active)

    @patch("punjab_estamp.services.sync.fetch_tehsil_options")
    def test_sync_can_explicitly_mark_missing_tehsils_inactive(self, fetch_mock):
        old = PunjabEStampTehsil.objects.create(
            district=self.district, name="Legacy Verified", portal_value="999", active=True
        )
        fetch_mock.return_value = [TehsilOption("Lahore City", "101")]

        report = sync_district_tehsils(self.district, mark_missing_inactive=True)

        old.refresh_from_db()
        self.assertFalse(old.active)
        self.assertEqual(report["deactivated"], 1)

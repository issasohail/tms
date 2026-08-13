from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ReorganizeUnitMediaPathsMigrationTests(TransactionTestCase):
    migrate_from = ("properties", "0027_unit_internet_and_security_deposit_amount")
    migrate_to = ("properties", "0028_reorganize_unit_media_paths")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        self.old_apps = executor.loader.project_state([self.migrate_from]).apps
        self.Property = self.old_apps.get_model("properties", "Property")
        self.Unit = self.old_apps.get_model("properties", "Unit")
        self.UnitMedia = self.old_apps.get_model("properties", "UnitMedia")
        self.PropertyMedia = self.old_apps.get_model("properties", "PropertyMedia")

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        super().tearDown()

    def _property_and_unit(self, property_name="F35", unit_number="F35-FLAT# 03"):
        property_obj = self.Property.objects.create(
            property_name=property_name,
            owner_name="Owner",
            owner_cnic="35202-1234567-1",
            type="Building",
            property_type="apartment",
            total_units=1,
        )
        unit = self.Unit.objects.create(
            property=property_obj,
            unit_number=unit_number,
        )
        return property_obj, unit

    def _migrate_forward(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        return executor.loader.project_state([self.migrate_to]).apps

    def test_legacy_id_paths_for_all_fields_become_canonical(self):
        _, unit = self._property_and_unit(unit_number="F35-FLAT# 01")
        media = self.UnitMedia.objects.create(
            unit=unit,
            file="properties/unit-86/original/photo.jpg",
            stamped_file="properties/unit-86/stamped/photo-stamped.jpg",
            thumbnail="properties/unit-86/thumbs/photo-thumb.jpg",
        )

        new_apps = self._migrate_forward()
        migrated = new_apps.get_model("properties", "UnitMedia").objects.get(
            pk=media.pk
        )

        self.assertEqual(
            migrated.file.name,
            "properties/F35/units/F35-FLAT-01/original/photo.jpg",
        )
        self.assertEqual(
            migrated.stamped_file.name,
            "properties/F35/units/F35-FLAT-01/stamped/photo-stamped.jpg",
        )
        self.assertEqual(
            migrated.thumbnail.name,
            "properties/F35/units/F35-FLAT-01/thumbs/photo-thumb.jpg",
        )

    def test_duplicated_property_path_becomes_canonical_and_keeps_filename(self):
        _, unit = self._property_and_unit()
        media = self.UnitMedia.objects.create(
            unit=unit,
            thumbnail=(
                "properties/F35-F35-FLAT-03/thumbs/custom-photo-thumb.jpg"
            ),
        )

        new_apps = self._migrate_forward()
        migrated = new_apps.get_model("properties", "UnitMedia").objects.get(
            pk=media.pk
        )

        self.assertEqual(
            migrated.thumbnail.name,
            "properties/F35/units/F35-FLAT-03/thumbs/custom-photo-thumb.jpg",
        )

    def test_already_canonical_path_is_unchanged(self):
        _, unit = self._property_and_unit()
        canonical = "properties/F35/units/F35-FLAT-03/original/photo.jpg"
        media = self.UnitMedia.objects.create(unit=unit, file=canonical)

        new_apps = self._migrate_forward()
        migrated = new_apps.get_model("properties", "UnitMedia").objects.get(
            pk=media.pk
        )

        self.assertEqual(migrated.file.name, canonical)

    def test_blank_file_fields_are_unchanged(self):
        _, unit = self._property_and_unit()
        media = self.UnitMedia.objects.create(
            unit=unit,
            file="",
            stamped_file="",
            thumbnail="",
        )

        new_apps = self._migrate_forward()
        migrated = new_apps.get_model("properties", "UnitMedia").objects.get(
            pk=media.pk
        )

        self.assertEqual(migrated.file.name, "")
        self.assertEqual(migrated.stamped_file.name, "")
        self.assertEqual(migrated.thumbnail.name, "")

    def test_normalization_is_frozen_for_hash_whitespace_and_punctuation(self):
        _, unit = self._property_and_unit(
            property_name="  F35   Block  ",
            unit_number="  F35---FLAT# 03  ",
        )
        media = self.UnitMedia.objects.create(
            unit=unit,
            file="properties/unit-88/original/tenant photo.jpg",
        )

        new_apps = self._migrate_forward()
        migrated = new_apps.get_model("properties", "UnitMedia").objects.get(
            pk=media.pk
        )

        self.assertEqual(
            migrated.file.name,
            "properties/F35-Block/units/F35-FLAT-03/original/tenant photo.jpg",
        )

    def test_blank_names_use_fallbacks_and_long_names_are_truncated(self):
        _, blank_unit = self._property_and_unit(
            property_name="   ",
            unit_number="   ",
        )
        blank_media = self.UnitMedia.objects.create(
            unit=blank_unit,
            file="properties/unit-old/original/blank.jpg",
        )
        _, long_unit = self._property_and_unit(
            property_name="P" * 50,
            unit_number="U" * 20,
        )
        long_media = self.UnitMedia.objects.create(
            unit=long_unit,
            file="properties/unit-long/original/long.jpg",
        )

        new_apps = self._migrate_forward()
        NewUnitMedia = new_apps.get_model("properties", "UnitMedia")

        self.assertEqual(
            NewUnitMedia.objects.get(pk=blank_media.pk).file.name,
            f"properties/property/units/unit-{blank_unit.pk}/original/blank.jpg",
        )
        self.assertEqual(
            NewUnitMedia.objects.get(pk=long_media.pk).file.name,
            f"properties/{'P' * 42}/units/{'U' * 20}/original/long.jpg",
        )

    def test_property_media_is_unchanged(self):
        property_obj, _ = self._property_and_unit()
        old_path = "properties/F35/original/property-photo.jpg"
        media = self.PropertyMedia.objects.create(
            property=property_obj,
            file=old_path,
        )

        new_apps = self._migrate_forward()
        migrated = new_apps.get_model("properties", "PropertyMedia").objects.get(
            pk=media.pk
        )

        self.assertEqual(migrated.file.name, old_path)

    def test_unrelated_unit_media_fields_are_unchanged(self):
        _, unit = self._property_and_unit()
        media = self.UnitMedia.objects.create(
            unit=unit,
            file="properties/unit-88/original/report.pdf",
            file_type="file",
            description="Keep this description",
            sort_order=17,
            is_active=False,
            original_filename="Original Report.pdf",
        )

        new_apps = self._migrate_forward()
        migrated = new_apps.get_model("properties", "UnitMedia").objects.get(
            pk=media.pk
        )

        self.assertEqual(migrated.file_type, "file")
        self.assertEqual(migrated.description, "Keep this description")
        self.assertEqual(migrated.sort_order, 17)
        self.assertFalse(migrated.is_active)
        self.assertEqual(migrated.original_filename, "Original Report.pdf")

import hashlib
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings


class UnitMediaReorganizationWorkflowTests(TransactionTestCase):
    migrate_from = ("properties", "0027_unit_internet_and_security_deposit_amount")
    migrate_to = ("properties", "0028_reorganize_unit_media_paths")
    field_names = ("file", "stamped_file", "thumbnail")

    def setUp(self):
        super().setUp()
        self._media_directory = tempfile.TemporaryDirectory()
        self._media_override = override_settings(MEDIA_ROOT=self._media_directory.name)
        self._media_override.enable()

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        self.old_apps = executor.loader.project_state([self.migrate_from]).apps
        self.Property = self.old_apps.get_model("properties", "Property")
        self.Unit = self.old_apps.get_model("properties", "Unit")
        self.UnitMedia = self.old_apps.get_model("properties", "UnitMedia")
        self.PropertyMedia = self.old_apps.get_model("properties", "PropertyMedia")

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("properties", "0031_seed_two_room_flat_move_out_charges")])

        self._media_override.disable()
        self._media_directory.cleanup()
        super().tearDown()

    def _path(self, relative_name):
        return Path(self._media_directory.name, *relative_name.split("/"))

    def _write(self, relative_name, content):
        path = self._path(relative_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _run_prepare(self, *args):
        stdout = StringIO()
        stderr = StringIO()
        call_command(
            "prepare_unit_media_reorganization",
            *args,
            stdout=stdout,
            stderr=stderr,
        )
        return stdout.getvalue(), stderr.getvalue()

    def _create_property(self, pk, property_name):
        return self.Property.objects.create(
            pk=pk,
            property_name=property_name,
            owner_name="Workflow Owner",
            owner_cnic=f"35202-12345{pk:02d}-1"[-15:],
            type="Building",
            property_type="apartment",
            total_units=3,
        )

    def _db_paths(self, model):
        return {
            media.pk: {
                field_name: str(getattr(media, field_name) or "")
                for field_name in self.field_names
            }
            for media in model.objects.order_by("pk")
        }

    def test_complete_prepare_then_migrate_workflow(self):
        property_f56 = self._create_property(56, "F56")
        property_f35 = self._create_property(35, "F35")
        unit17 = self.Unit.objects.create(
            pk=17,
            property=property_f56,
            unit_number="F56-FLAT# 05",
        )
        unit86 = self.Unit.objects.create(
            pk=86,
            property=property_f35,
            unit_number="F35-FLAT# 01",
        )
        unit88 = self.Unit.objects.create(
            pk=88,
            property=property_f35,
            unit_number="F35-FLAT# 03",
        )

        media17 = self.UnitMedia.objects.create(
            unit=unit17,
            file="properties/unit-17/original/unit17.jpg",
            stamped_file="properties/unit-17/stamped/unit17-stamped.jpg",
            thumbnail="properties/unit-17/thumbs/unit17-thumb.jpg",
            description="Unit 17 workflow fixture",
            sort_order=17,
        )
        media86 = self.UnitMedia.objects.create(
            unit=unit86,
            file="properties/unit-86/original/photo.jpg",
            description="Unit 86 workflow fixture",
        )
        media88 = self.UnitMedia.objects.create(
            unit=unit88,
            thumbnail="properties/unit-88/thumbs/unit88-thumb.jpg",
            description="Unit 88 workflow fixture",
        )
        duplicated = self.UnitMedia.objects.create(
            unit=unit88,
            stamped_file=("properties/F35-F35-FLAT-03/stamped/duplicate-stamped.jpg"),
            description="Duplicated path fixture",
        )
        canonical_name = "properties/F35/units/F35-FLAT-03/original/canonical.jpg"
        canonical = self.UnitMedia.objects.create(
            unit=unit88,
            file=canonical_name,
            description="Already canonical fixture",
        )

        property_media_name = "properties/F35/original/property-only.jpg"
        property_media = self.PropertyMedia.objects.create(
            property=property_f35,
            file=property_media_name,
            description="Must remain untouched",
        )

        expected_mappings = {
            "properties/unit-17/original/unit17.jpg": (
                "properties/F56/units/F56-FLAT-05/original/unit17.jpg"
            ),
            "properties/unit-17/stamped/unit17-stamped.jpg": (
                "properties/F56/units/F56-FLAT-05/stamped/unit17-stamped.jpg"
            ),
            "properties/unit-17/thumbs/unit17-thumb.jpg": (
                "properties/F56/units/F56-FLAT-05/thumbs/unit17-thumb.jpg"
            ),
            "properties/unit-86/original/photo.jpg": (
                "properties/F35/units/F35-FLAT-01/original/photo.jpg"
            ),
            "properties/unit-88/thumbs/unit88-thumb.jpg": (
                "properties/F35/units/F35-FLAT-03/thumbs/unit88-thumb.jpg"
            ),
            "properties/F35-F35-FLAT-03/stamped/duplicate-stamped.jpg": (
                "properties/F35/units/F35-FLAT-03/stamped/duplicate-stamped.jpg"
            ),
        }
        source_contents = {
            old_name: f"workflow:{index}:{old_name}".encode()
            for index, old_name in enumerate(expected_mappings, start=1)
        }
        for old_name, content in source_contents.items():
            self._write(old_name, content)
        self._write(canonical_name, b"already canonical content")
        property_source = self._write(property_media_name, b"property media content")

        # Phase A: every non-empty source exists and the DB contains legacy paths.
        before_paths = self._db_paths(self.UnitMedia)
        expected_records = {
            media17.pk,
            media86.pk,
            media88.pk,
            duplicated.pk,
            canonical.pk,
        }
        self.assertEqual(set(before_paths), expected_records)
        for paths in before_paths.values():
            for stored_name in paths.values():
                if stored_name:
                    self.assertTrue(self._path(stored_name).is_file())
        for old_name in expected_mappings:
            self.assertIn(
                old_name,
                {
                    stored_name
                    for paths in before_paths.values()
                    for stored_name in paths.values()
                },
            )

        # Phase B: dry-run reports a clean plan and changes nothing.
        dry_run_output, dry_run_errors = self._run_prepare("--dry-run")
        self.assertEqual(dry_run_errors, "")
        self.assertIn("UnitMedia records examined: 5", dry_run_output)
        self.assertIn("Non-empty file fields examined: 7", dry_run_output)
        self.assertIn("Already canonical: 1", dry_run_output)
        self.assertIn("Copy required: 6", dry_run_output)
        self.assertIn("Already prepared: 0", dry_run_output)
        self.assertIn("Missing sources: 0", dry_run_output)
        self.assertIn("Collisions: 0", dry_run_output)
        self.assertIn("Errors: 0", dry_run_output)
        self.assertIn("Bytes copied: 0", dry_run_output)
        self.assertIn("DRY RUN - NO FILES CHANGED", dry_run_output)
        self.assertEqual(self._db_paths(self.UnitMedia), before_paths)
        for destination in expected_mappings.values():
            self.assertFalse(self._path(destination).exists())

        # Phase C: apply copies and verifies files without changing DB or sources.
        apply_output, apply_errors = self._run_prepare("--apply")
        self.assertEqual(apply_errors, "")
        self.assertIn("Copy required: 6", apply_output)
        self.assertIn("Missing sources: 0", apply_output)
        self.assertIn("Collisions: 0", apply_output)
        self.assertIn("Errors: 0", apply_output)
        self.assertIn("DATABASE CHANGED: NO", apply_output)
        self.assertIn("SOURCE FILES DELETED: NO", apply_output)
        self.assertEqual(self._db_paths(self.UnitMedia), before_paths)

        expected_bytes = sum(len(content) for content in source_contents.values())
        self.assertIn(f"Bytes to copy: {expected_bytes}", apply_output)
        self.assertIn(f"Bytes copied: {expected_bytes}", apply_output)
        for old_name, new_name in expected_mappings.items():
            source = self._path(old_name)
            destination = self._path(new_name)
            self.assertTrue(source.is_file())
            self.assertTrue(destination.is_file())
            self.assertEqual(
                hashlib.sha256(source.read_bytes()).hexdigest(),
                hashlib.sha256(destination.read_bytes()).hexdigest(),
            )

        second_output, second_errors = self._run_prepare("--apply")
        self.assertEqual(second_errors, "")
        self.assertIn("Already prepared: 6", second_output)
        self.assertIn("Copy required: 0", second_output)
        self.assertIn("Bytes to copy: 0", second_output)
        self.assertIn("Bytes copied: 0", second_output)
        self.assertEqual(self._db_paths(self.UnitMedia), before_paths)

        property_media.refresh_from_db()
        self.assertEqual(property_media.file.name, property_media_name)
        self.assertTrue(property_source.is_file())

        # Phase D: apply only migration 0028 inside this disposable test DB.
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        new_apps = executor.loader.project_state([self.migrate_to]).apps
        NewUnitMedia = new_apps.get_model("properties", "UnitMedia")
        NewPropertyMedia = new_apps.get_model("properties", "PropertyMedia")
        after_paths = self._db_paths(NewUnitMedia)

        for old_name, new_name in expected_mappings.items():
            self.assertIn(
                new_name,
                {
                    stored_name
                    for paths in after_paths.values()
                    for stored_name in paths.values()
                },
            )
            self.assertNotIn(
                old_name,
                {
                    stored_name
                    for paths in after_paths.values()
                    for stored_name in paths.values()
                },
            )
        self.assertEqual(after_paths[canonical.pk]["file"], canonical_name)

        migrated17 = NewUnitMedia.objects.get(pk=media17.pk)
        self.assertEqual(migrated17.description, "Unit 17 workflow fixture")
        self.assertEqual(migrated17.sort_order, 17)
        migrated_property_media = NewPropertyMedia.objects.get(pk=property_media.pk)
        self.assertEqual(migrated_property_media.file.name, property_media_name)
        self.assertEqual(
            migrated_property_media.description,
            "Must remain untouched",
        )

        # Phase E: every migrated FileField resolves to readable physical media.
        missing_files = 0
        storage_errors = 0
        for media in NewUnitMedia.objects.order_by("pk"):
            for field_name in self.field_names:
                stored_name = str(getattr(media, field_name) or "")
                if not stored_name:
                    continue
                try:
                    stored_path = self._path(stored_name)
                    if not stored_path.is_file():
                        missing_files += 1
                    else:
                        stored_path.read_bytes()
                except OSError:
                    storage_errors += 1

        self.assertEqual(missing_files, 0)
        self.assertEqual(storage_errors, 0)
        for old_name in expected_mappings:
            self.assertTrue(self._path(old_name).is_file())

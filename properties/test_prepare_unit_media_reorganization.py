import hashlib
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from .models import Property, PropertyMedia, Unit, UnitMedia


class PrepareUnitMediaReorganizationTests(TestCase):
    def setUp(self):
        self._media_directory = tempfile.TemporaryDirectory()
        self._media_override = override_settings(MEDIA_ROOT=self._media_directory.name)
        self._media_override.enable()
        self.property = Property.objects.create(
            property_name="F35",
            owner_name="Owner",
            owner_cnic="35202-1234567-1",
            type="Building",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="F35-FLAT# 03",
        )

    def tearDown(self):
        self._media_override.disable()
        self._media_directory.cleanup()
        super().tearDown()

    def _write(self, relative_name, content=b"unit media"):
        path = Path(self._media_directory.name, *relative_name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _path(self, relative_name):
        return Path(self._media_directory.name, *relative_name.split("/"))

    def _create_unit_media(self, **field_names):
        media = UnitMedia.objects.create(unit=self.unit, file="")
        UnitMedia.objects.filter(pk=media.pk).update(**field_names)
        media.refresh_from_db()
        return media

    def _run(self, *args):
        stdout = StringIO()
        stderr = StringIO()
        call_command(
            "prepare_unit_media_reorganization",
            *args,
            stdout=stdout,
            stderr=stderr,
        )
        return stdout.getvalue(), stderr.getvalue()

    def _run_refused(self):
        stdout = StringIO()
        stderr = StringIO()
        with self.assertRaises(CommandError):
            call_command(
                "prepare_unit_media_reorganization",
                "--apply",
                stdout=stdout,
                stderr=stderr,
            )
        return stdout.getvalue(), stderr.getvalue()

    def test_dry_run_is_default_and_changes_neither_filesystem_nor_database(self):
        old_name = "properties/unit-88/original/photo.jpg"
        new_name = "properties/F35/units/F35-FLAT-03/original/photo.jpg"
        self._write(old_name)
        media = self._create_unit_media(file=old_name)

        output, _ = self._run()

        media.refresh_from_db()
        self.assertEqual(media.file.name, old_name)
        self.assertTrue(self._path(old_name).exists())
        self.assertFalse(self._path(new_name).exists())
        self.assertIn("Proposed action: COPY_REQUIRED", output)
        self.assertIn("DRY RUN - NO FILES CHANGED", output)

    def test_apply_copies_original_and_preserves_db_path_and_source(self):
        old_name = "properties/unit-88/original/photo.jpg"
        new_name = "properties/F35/units/F35-FLAT-03/original/photo.jpg"
        source = self._write(old_name, b"original bytes")
        media = self._create_unit_media(file=old_name)

        output, _ = self._run("--apply")

        media.refresh_from_db()
        destination = self._path(new_name)
        self.assertEqual(media.file.name, old_name)
        self.assertTrue(source.exists())
        self.assertTrue(destination.exists())
        self.assertEqual(
            hashlib.sha256(source.read_bytes()).hexdigest(),
            hashlib.sha256(destination.read_bytes()).hexdigest(),
        )
        self.assertIn("DATABASE CHANGED: NO", output)
        self.assertIn("SOURCE FILES DELETED: NO", output)

    def test_apply_copies_stamped_file(self):
        old_name = "properties/unit-88/stamped/photo-stamped.jpg"
        new_name = (
            "properties/F35/units/F35-FLAT-03/stamped/photo-stamped.jpg"
        )
        self._write(old_name, b"stamped bytes")
        media = self._create_unit_media(stamped_file=old_name)

        self._run("--apply")

        media.refresh_from_db()
        self.assertEqual(media.stamped_file.name, old_name)
        self.assertEqual(self._path(new_name).read_bytes(), b"stamped bytes")

    def test_apply_copies_thumbnail(self):
        old_name = "properties/unit-88/thumbs/photo-thumb.jpg"
        new_name = "properties/F35/units/F35-FLAT-03/thumbs/photo-thumb.jpg"
        self._write(old_name, b"thumbnail bytes")
        media = self._create_unit_media(thumbnail=old_name)

        self._run("--apply")

        media.refresh_from_db()
        self.assertEqual(media.thumbnail.name, old_name)
        self.assertEqual(self._path(new_name).read_bytes(), b"thumbnail bytes")

    def test_identical_destination_is_already_prepared(self):
        old_name = "properties/unit-88/original/photo.jpg"
        new_name = "properties/F35/units/F35-FLAT-03/original/photo.jpg"
        self._write(old_name, b"same bytes")
        self._write(new_name, b"same bytes")
        self._create_unit_media(file=old_name)

        output, _ = self._run("--apply")

        self.assertIn("Proposed action: ALREADY_PREPARED", output)
        self.assertIn("Already prepared: 1", output)
        self.assertIn("Bytes copied: 0", output)

    def test_different_destination_is_collision_and_refuses_all_copying(self):
        collision_old = "properties/unit-88/original/photo.jpg"
        collision_new = "properties/F35/units/F35-FLAT-03/original/photo.jpg"
        copy_old = "properties/unit-88/thumbs/photo-thumb.jpg"
        copy_new = "properties/F35/units/F35-FLAT-03/thumbs/photo-thumb.jpg"
        self._write(collision_old, b"source")
        self._write(collision_new, b"different")
        self._write(copy_old, b"copy candidate")
        self._create_unit_media(file=collision_old, thumbnail=copy_old)

        output, _ = self._run_refused()

        self.assertFalse(self._path(copy_new).exists())
        self.assertEqual(self._path(collision_new).read_bytes(), b"different")
        self.assertIn("Proposed action: COLLISION", output)
        self.assertIn("Collisions: 1", output)

    def test_plan_level_different_sources_same_destination_refuses_before_copy(self):
        first_old = "properties/unit-88/original/shared.jpg"
        second_old = "properties/F35-F35-FLAT-03/original/shared.jpg"
        destination = "properties/F35/units/F35-FLAT-03/original/shared.jpg"
        first_source = self._write(first_old, b"first source")
        second_source = self._write(second_old, b"different second source")
        first_media = self._create_unit_media(file=first_old)
        second_media = self._create_unit_media(file=second_old)
        before_paths = {
            first_media.pk: first_media.file.name,
            second_media.pk: second_media.file.name,
        }

        dry_run_output, _ = self._run("--dry-run")

        self.assertIn("Proposed action: COLLISION", dry_run_output)
        self.assertIn("Collisions: 2", dry_run_output)
        self.assertFalse(self._path(destination).exists())

        apply_output, _ = self._run_refused()

        first_media.refresh_from_db()
        second_media.refresh_from_db()
        self.assertEqual(first_media.file.name, before_paths[first_media.pk])
        self.assertEqual(second_media.file.name, before_paths[second_media.pk])
        self.assertTrue(first_source.exists())
        self.assertTrue(second_source.exists())
        self.assertFalse(self._path(destination).exists())
        self.assertIn("Collisions: 2", apply_output)
        self.assertIn("Bytes copied: 0", apply_output)

    def test_plan_level_identical_sources_same_destination_copy_once(self):
        first_old = "properties/unit-88/original/shared.jpg"
        second_old = "properties/F35-F35-FLAT-03/original/shared.jpg"
        destination = "properties/F35/units/F35-FLAT-03/original/shared.jpg"
        content = b"identical source content"
        first_source = self._write(first_old, content)
        second_source = self._write(second_old, content)
        first_media = self._create_unit_media(file=first_old)
        second_media = self._create_unit_media(file=second_old)

        output, _ = self._run("--apply")

        destination_path = self._path(destination)
        first_media.refresh_from_db()
        second_media.refresh_from_db()
        self.assertEqual(first_media.file.name, first_old)
        self.assertEqual(second_media.file.name, second_old)
        self.assertTrue(first_source.exists())
        self.assertTrue(second_source.exists())
        self.assertTrue(destination_path.exists())
        self.assertEqual(
            hashlib.sha256(destination_path.read_bytes()).hexdigest(),
            hashlib.sha256(content).hexdigest(),
        )
        self.assertIn("Copy required: 1", output)
        self.assertIn("Duplicate mappings: 1", output)
        self.assertIn("Collisions: 0", output)
        self.assertIn(f"Bytes copied: {len(content)}", output)

        second_output, _ = self._run("--apply")

        self.assertIn("Copy required: 0", second_output)
        self.assertIn("Already prepared: 2", second_output)
        self.assertIn("Collisions: 0", second_output)
        self.assertIn("Bytes copied: 0", second_output)

    def test_missing_source_refuses_all_copying(self):
        missing_name = "properties/unit-88/original/missing.jpg"
        copy_old = "properties/unit-88/thumbs/photo-thumb.jpg"
        copy_new = "properties/F35/units/F35-FLAT-03/thumbs/photo-thumb.jpg"
        self._write(copy_old, b"copy candidate")
        self._create_unit_media(file=missing_name, thumbnail=copy_old)

        output, _ = self._run_refused()

        self.assertFalse(self._path(copy_new).exists())
        self.assertIn("Proposed action: MISSING_SOURCE", output)
        self.assertIn("Missing sources: 1", output)

    def test_repeated_apply_is_idempotent(self):
        old_name = "properties/unit-88/original/photo.jpg"
        new_name = "properties/F35/units/F35-FLAT-03/original/photo.jpg"
        self._write(old_name, b"repeatable")
        self._create_unit_media(file=old_name)

        first_output, _ = self._run("--apply")
        second_output, _ = self._run("--apply")

        self.assertIn("Copy required: 1", first_output)
        self.assertIn("Already prepared: 1", second_output)
        self.assertIn("Bytes copied: 0", second_output)
        self.assertEqual(self._path(new_name).read_bytes(), b"repeatable")

    def test_already_canonical_db_path_is_skipped_safely(self):
        canonical_name = (
            "properties/F35/units/F35-FLAT-03/original/photo.jpg"
        )
        self._write(canonical_name, b"canonical")
        media = self._create_unit_media(file=canonical_name)

        output, _ = self._run("--apply")

        media.refresh_from_db()
        self.assertEqual(media.file.name, canonical_name)
        self.assertIn("Proposed action: ALREADY_CANONICAL", output)
        self.assertIn("Already canonical: 1", output)
        self.assertIn("Bytes copied: 0", output)

    def test_property_media_is_untouched(self):
        property_old = "properties/F35/original/property-photo.jpg"
        property_new = (
            "properties/F35/units/F35-FLAT-03/original/property-photo.jpg"
        )
        source = self._write(property_old, b"property media")
        property_media = PropertyMedia.objects.create(
            property=self.property,
            file="",
        )
        PropertyMedia.objects.filter(pk=property_media.pk).update(file=property_old)

        output, _ = self._run("--apply")

        property_media.refresh_from_db()
        self.assertEqual(property_media.file.name, property_old)
        self.assertTrue(source.exists())
        self.assertFalse(self._path(property_new).exists())
        self.assertIn("UnitMedia records examined: 0", output)

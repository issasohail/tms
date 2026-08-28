import subprocess
import zipfile
import gzip
import sys
import os
from io import BytesIO
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.backup_utils import (
    BackupItem,
    _is_mysql_concurrent_ddl_error,
    _mysql_client_environment,
    _mysqldump_compatibility_args,
    _run_mysql_command,
    _run_mysql_dump_to_gzip,
    create_db_backup,
    detect_uploaded_backup_type,
    list_backups,
    prune_old_backups,
    purge_old_backups,
)
from core.views import _pending_approval_filter_state
from core.forms import GlobalSettingsForm
from core.models import GlobalSettings, TenantIncomeBracket, TenantOccupationOption


class SettingsConsolidationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="settings-query-admin",
            email="settings-query@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)

    def test_whatsapp_staff_selectors_share_one_account_query(self):
        field_names = (
            "whatsapp_default_support_staff",
            "whatsapp_accounts_staff",
            "whatsapp_maintenance_staff",
            "whatsapp_leasing_staff",
            "whatsapp_escalation_staff",
        )

        cache.delete("core.global_settings")
        cache.delete("core.settings_whatsapp_account_choices")
        with CaptureQueriesContext(connection) as queries:
            form = GlobalSettingsForm(instance=GlobalSettings.get_solo())
            rendered = "".join(str(form[field_name]) for field_name in field_names)

        account_queries = [
            query["sql"] for query in queries.captured_queries
            if " from `accounts_account`" in query["sql"].lower()
        ]
        self.assertTrue(rendered)
        self.assertEqual(len(account_queries), 1)

        cache.delete("core.global_settings")
        with CaptureQueriesContext(connection) as warm_queries:
            warm_form = GlobalSettingsForm(instance=GlobalSettings.get_solo())
            "".join(str(warm_form[field_name]) for field_name in field_names)
        warm_account_queries = [
            query["sql"] for query in warm_queries.captured_queries
            if " from `accounts_account`" in query["sql"].lower()
        ]
        self.assertEqual(warm_account_queries, [])

    def test_settings_combines_sections_and_opens_whatsapp_tools_in_new_windows(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("core:settings"))

        self.assertEqual(response.status_code, 200)
        group_titles = [title for title, _icon, _fields in response.context["settings_field_groups"]]
        self.assertIn("General, Billing & Operations", group_titles)
        self.assertNotIn("Reference Data", group_titles)
        self.assertNotIn("Parking & Water Penalties", group_titles)
        self.assertNotIn("Police Verification", group_titles)
        self.assertNotIn("Tenant Registration", group_titles)
        self.assertIn("WhatsApp / Twilio", group_titles)
        self.assertNotIn("Late Fees", group_titles)
        self.assertNotIn("WhatsApp AI Assistant", group_titles)
        self.assertContains(response, "General &amp; Billing")
        self.assertContains(response, "Monthly Billing Time")
        self.assertContains(response, "Late Fee Reminder Time")
        self.assertContains(response, "Utility Templates")
        self.assertContains(response, "Webhook Logs")
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, 'data-settings-target="settings-reference-data"')
        self.assertContains(response, 'id="settings-reference-toolbar"')
        self.assertContains(response, "settings-help-icon")
        self.assertContains(response, "grid-template-columns: repeat(10")
        self.assertContains(response, "settings-reference-grid")
        self.assertContains(response, "Monthly Income / Salary Brackets")
        self.assertContains(response, "Occupation Suggestions")
        self.assertContains(response, "Police document category code")
        self.assertContains(response, "Tenant cnic ocr enabled")
        self.assertContains(response, "?embed=1")
        self.assertContains(response, 'data-settings-target="settings-global-inventory-defaults"')
        self.assertContains(response, 'data-settings-target="settings-move-out-charges"')
        self.assertContains(response, 'data-settings-target="settings-invoice-expense-categories"')
        self.assertContains(response, "Invoice &amp; Expense Categories")
        self.assertContains(response, "settings-reference-frame")
        self.assertContains(response, "max-width: 960px")
        self.assertContains(response, "settings-family-legend")
        self.assertNotContains(response, 'data-settings-target="settings-tool-suggestions"')
        self.assertNotContains(response, 'data-settings-target="settings-tool-pending-approvals"')
        self.assertNotContains(response, 'src="about:blank"')
        self.assertNotContains(
            response,
            'data-settings-target="settings-group-late-fees"',
        )
        building_type_queries = [
            query["sql"] for query in queries.captured_queries
            if "properties_buildingtype" in query["sql"].lower()
        ]
        self.assertEqual(len(building_type_queries), 1)

    def test_tenant_reference_values_support_create_inline_update_and_delete(self):
        create_response = self.client.post(
            reverse("core:tenant_reference_create", args=["income"]),
            {"name": "150,000 and more", "sort_order": "70", "is_active": "1"},
        )
        self.assertEqual(create_response.status_code, 200)
        bracket = TenantIncomeBracket.objects.get(name="150,000 and more")

        update_response = self.client.post(
            reverse("core:tenant_reference_inline_update", args=["income", bracket.pk]),
            {"field": "sort_order", "value": "15"},
        )
        self.assertEqual(update_response.status_code, 200)
        bracket.refresh_from_db()
        self.assertEqual(bracket.sort_order, 15)

        occupation = TenantOccupationOption.objects.create(name="Engineer")
        active_response = self.client.post(
            reverse("core:tenant_reference_inline_update", args=["occupation", occupation.pk]),
            {"field": "is_active", "value": "0"},
        )
        self.assertEqual(active_response.status_code, 200)
        occupation.refresh_from_db()
        self.assertFalse(occupation.is_active)

        delete_response = self.client.post(
            reverse("core:tenant_reference_delete", args=["income", bracket.pk])
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(TenantIncomeBracket.objects.filter(pk=bracket.pk).exists())


class PendingApprovalDetailTemplateTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template_source = (
            Path(__file__).resolve().parent
            / "templates"
            / "core"
            / "pending_approval_detail.html"
        ).read_text(encoding="utf-8")

    def test_success_alert_has_a_safe_fallback_container(self):
        self.assertIn('const alertHost = form.closest(".card")', self.template_source)
        self.assertIn('|| form.closest(".container-fluid")', self.template_source)
        self.assertIn("alertHost.prepend(alert);", self.template_source)
        self.assertNotIn('form.closest(".card").prepend(alert);', self.template_source)


class PendingApprovalFilterTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_defaults_to_pending_status_and_all_dates(self):
        filters = _pending_approval_filter_state(self.factory.get("/pending-approvals/"))

        self.assertEqual(filters["status"], "pending")
        self.assertEqual(filters["approval_type"], "all")
        self.assertEqual(filters["date_range"], "all")
        self.assertIsNone(filters["date_from"])
        self.assertIsNone(filters["date_to"])

    @patch("core.views.timezone.localdate", return_value=date(2026, 7, 19))
    def test_last_week_uses_previous_monday_to_sunday(self, _localdate):
        request = self.factory.get("/pending-approvals/", {"date_range": "last_week"})

        filters = _pending_approval_filter_state(request)

        self.assertEqual(filters["date_from"], date(2026, 7, 6))
        self.assertEqual(filters["date_to"], date(2026, 7, 12))

    def test_custom_reversed_dates_are_normalized(self):
        request = self.factory.get(
            "/pending-approvals/",
            {"date_range": "custom", "date_from": "2026-07-19", "date_to": "2026-07-01"},
        )

        filters = _pending_approval_filter_state(request)

        self.assertEqual(filters["date_from"], date(2026, 7, 1))
        self.assertEqual(filters["date_to"], date(2026, 7, 19))

    def test_property_unit_and_text_search_are_normalized(self):
        request = self.factory.get(
            "/pending-approvals/",
            {"property": "12", "unit": "34", "q": "  receipt  "},
        )

        filters = _pending_approval_filter_state(request)

        self.assertEqual(filters["property_id"], 12)
        self.assertEqual(filters["unit_id"], 34)
        self.assertEqual(filters["search"], "receipt")

    def test_approval_type_is_validated(self):
        selected = _pending_approval_filter_state(
            self.factory.get("/pending-approvals/", {"approval_type": "family"})
        )
        invalid = _pending_approval_filter_state(
            self.factory.get("/pending-approvals/", {"approval_type": "unknown"})
        )

        self.assertEqual(selected["approval_type"], "family")
        self.assertEqual(invalid["approval_type"], "all")


class PendingApprovalLeaseScopeTests(TestCase):
    def setUp(self):
        from properties.models import Property, Unit
        from tenants.models import Tenant

        self.user = get_user_model().objects.create_superuser(
            "approval-scope", email="approval-scope@example.com", password="test"
        )
        self.client.force_login(self.user)
        self.property = Property.objects.create(
            property_name="Scope Property",
            owner_name="Owner",
            owner_cnic="37405-7654321-1",
            type="Residential",
            property_type="apartment",
            total_units=2,
        )
        self.unit = Unit.objects.create(property=self.property, unit_number="S-01", status="occupied")
        self.tenant = Tenant.objects.create(
            first_name="Scope", last_name="Tenant", phone="+923001234567", cnic="37405-1234567-1"
        )

    def _lease(self, status):
        from leases.models import Lease

        return Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=timezone.localdate() - timedelta(days=30),
            end_date=timezone.localdate() + timedelta(days=335),
            monthly_rent=Decimal("25000"),
            status=status,
        )

    def test_all_status_excludes_unrelated_historical_active_leases(self):
        from whatsapp.models import WhatsAppExternalLinkToken

        historical = self._lease("active")
        linked = self._lease("active")
        pending = self._lease("pending_approval")
        WhatsAppExternalLinkToken.objects.create(
            link_type=WhatsAppExternalLinkToken.LINK_LEASE_CREATION,
            phone_number=self.tenant.phone,
            tenant=self.tenant,
            target_app_label="leases",
            target_model="lease",
            target_object_id=linked.pk,
            expires_at=timezone.now() + timedelta(days=1),
        )

        response = self.client.get(
            reverse("core:pending_approvals"),
            {"status": "all", "property": self.property.pk, "unit": self.unit.pk, "q": "Scope"},
        )

        self.assertEqual(response.status_code, 200)
        lease_section = next(section for section in response.context["sections"] if section["kind"] == "lease")
        shown_ids = {row["object"].pk for row in lease_section["items"]}
        self.assertNotIn(historical.pk, shown_ids)
        self.assertIn(linked.pk, shown_ids)
        self.assertIn(pending.pk, shown_ids)


class BackupMySQLCommandTests(SimpleTestCase):
    def test_streaming_gzip_dump_is_a_valid_gzip_file(self):
        payload = b"-- SQL dump\nINSERT INTO test VALUES (1);\n" * 1000
        with TemporaryDirectory() as backup_root:
            target = Path(backup_root) / "backup.sql.gz"
            _run_mysql_dump_to_gzip(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.buffer.write(b'-- SQL dump\\nINSERT INTO test VALUES (1);\\n' * 1000)",
                ],
                target,
            )

            self.assertEqual(target.read_bytes()[:2], b"\x1f\x8b")
            with gzip.open(target, "rb") as source:
                self.assertEqual(source.read(), payload)

    @patch("core.backup_utils.subprocess.run")
    def test_compatibility_options_follow_installed_client_help(self, run):
        run.return_value = subprocess.CompletedProcess(
            ["mysqldump", "--help"],
            0,
            stdout="--no-tablespaces\n--masking-policies",
        )

        self.assertEqual(
            _mysqldump_compatibility_args("mysqldump"),
            ["--no-tablespaces", "--skip-masking-policies"],
        )

    def test_password_is_passed_by_environment_not_command_argument(self):
        env = _mysql_client_environment({"PASSWORD": "secret-value"})

        self.assertEqual(env["MYSQL_PWD"], "secret-value")

    def test_concurrent_ddl_error_detection_is_specific(self):
        self.assertTrue(
            _is_mysql_concurrent_ddl_error(
                RuntimeError("Table was skipped due to a concurrent DDL statement (1684)")
            )
        )
        self.assertFalse(
            _is_mysql_concurrent_ddl_error(RuntimeError("Access denied for database user"))
        )

    @patch("core.backup_utils.time.sleep")
    @patch("core.backup_utils._run_mysql_command")
    @patch("core.backup_utils._mysqldump_compatibility_args", return_value=[])
    @patch("core.backup_utils._resolve_mysql_executable", return_value="mysqldump")
    @patch("core.backup_utils._db_settings")
    def test_database_backup_retries_after_concurrent_ddl_error(
        self,
        db_settings,
        _resolve_executable,
        _compatibility_args,
        run_mysql_command,
        sleep,
    ):
        db_settings.return_value = {
            "ENGINE": "django.db.backends.mysql",
            "NAME": "tenant_management",
            "HOST": "localhost",
            "PORT": "3306",
            "USER": "root",
            "PASSWORD": "",
        }
        run_mysql_command.side_effect = [
            RuntimeError("Database backup failed: concurrent DDL statement (1684)"),
            MagicMock(),
        ]

        with TemporaryDirectory() as backup_root:
            result = create_db_backup(
                {
                    "enable_db_backup": True,
                    "backup_root": backup_root,
                    "mysqldump_path": "mysqldump",
                    "compress_backups": False,
                }
            )

            self.assertTrue(result.exists())
            self.assertEqual(result.suffix, ".sql")
        self.assertEqual(run_mysql_command.call_count, 2)
        sleep.assert_called_once_with(2)

    @patch("core.backup_utils.subprocess.run")
    def test_mysql_stderr_is_returned_as_a_readable_error(self, run):
        run.side_effect = subprocess.CalledProcessError(
            3,
            ["mysqldump"],
            stderr="Access denied; PROCESS privilege required",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Database backup failed: Access denied; PROCESS privilege required",
        ):
            _run_mysql_command(
                ["mysqldump"],
                operation="Database backup",
                stdout=MagicMock(),
            )


class BackupUploadDetectionTests(SimpleTestCase):
    def _zip_upload(self, name, members):
        content = BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            for member in members:
                archive.writestr(member, "test")
        return SimpleUploadedFile(name, content.getvalue(), content_type="application/zip")

    def test_database_file_is_detected_from_extension(self):
        upload = SimpleUploadedFile("backup.sql", b"SELECT 1;")
        self.assertEqual(detect_uploaded_backup_type(upload), "db")

    def test_gzip_database_file_is_detected_from_extension(self):
        upload = SimpleUploadedFile("backup.sql.gz", b"gzip-placeholder")
        self.assertEqual(detect_uploaded_backup_type(upload), "db")

    def test_regular_zip_is_detected_as_media(self):
        upload = self._zip_upload("backup.zip", ["tenant_photos/photo.jpg"])
        self.assertEqual(detect_uploaded_backup_type(upload), "media")

    def test_full_package_zip_is_detected_from_contents(self):
        upload = self._zip_upload("backup.zip", ["full/tms_db_backup_test.sql"])
        self.assertEqual(detect_uploaded_backup_type(upload), "full")

    def test_purge_keeps_three_newest_files_overall(self):
        with TemporaryDirectory() as backup_root:
            root = Path(backup_root)
            for index in range(5):
                path = root / f"tms_db_backup_{index}.sql"
                path.write_text(str(index), encoding="utf-8")
                os.utime(path, (1000 + index, 1000 + index))
            for index in range(4):
                path = root / f"tms_media_backup_{index}.zip"
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("file.txt", str(index))
                os.utime(path, (2000 + index, 2000 + index))

            result = purge_old_backups({"backup_root": backup_root})
            remaining = [backup for backup in list_backups({"backup_root": backup_root}) if backup.file_exists]

            self.assertEqual(len(result["deleted"]), 6)
            self.assertEqual(len(remaining), 3)
            self.assertEqual(sum(backup.backup_type == "db" for backup in remaining), 0)
            self.assertEqual(sum(backup.backup_type == "media" for backup in remaining), 3)

    def test_automatic_pruning_can_be_disabled(self):
        with TemporaryDirectory() as backup_root:
            root = Path(backup_root)
            for index in range(4):
                (root / f"tms_db_backup_{index}.sql").write_text(str(index), encoding="utf-8")

            result = prune_old_backups({
                "backup_root": backup_root,
                "retention_count": 1,
                "auto_delete_old_backups": False,
            })

            self.assertEqual(result["deleted"], [])
            self.assertEqual(len([item for item in list_backups({"backup_root": backup_root}) if item.file_exists]), 4)


class BackupRestoreViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="backup-admin",
            email="backup@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)

    @patch("core.views.load_backup_settings")
    def test_uploaded_media_backup_is_selected_for_restore(self, load_settings):
        load_settings.return_value = {"backup_root": "C:/backup-test"}
        uploaded = MagicMock()
        uploaded.name = "media.zip"

        with patch("core.views.BackupUploadForm") as upload_form_class, patch(
            "core.views.detect_uploaded_backup_type",
            return_value="media",
        ), patch(
            "core.views.save_uploaded_backup",
            return_value=Path("C:/backup-test/tms_media_backup_uploaded_test.zip"),
        ):
            upload_form_class.return_value.is_valid.return_value = True
            upload_form_class.return_value.cleaned_data = {
                "backup_file": uploaded,
            }
            response = self.client.post(
                reverse("core:backup_center"),
                {"action": "upload_backup"},
            )

        self.assertRedirects(
            response,
            f"{reverse('core:backup_center')}?"
            "selected_backup=tms_media_backup_uploaded_test.zip#restore-backup",
            fetch_redirect_response=False,
        )

    def test_missing_file_keeps_serial_but_is_excluded_from_restore_choices(self):
        with TemporaryDirectory() as backup_root:
            root = Path(backup_root)
            database_file = root / "tms_db_backup_old.sql"
            missing_file = root / "tms_media_backup_new.zip"
            database_file.write_text("SELECT 1;", encoding="utf-8")
            with zipfile.ZipFile(missing_file, "w") as archive:
                archive.writestr("photo.jpg", "test")
            os.utime(database_file, (1000, 1000))
            os.utime(missing_file, (2000, 2000))
            config = {"backup_root": backup_root}
            list_backups(config)
            missing_file.unlink()

            with patch("core.views.load_backup_settings", return_value=config):
                response = self.client.get(reverse("core:backup_center"))

            backups = response.context["backups"]
            self.assertEqual(backups[0].id, missing_file.name)
            self.assertEqual(backups[0].serial_number, 1)
            self.assertFalse(backups[0].file_exists)
            restore_values = {
                value for value, _label in response.context["restore_form"].fields["backup_id"].choices
            }
            self.assertNotIn(missing_file.name, restore_values)
            self.assertIn(database_file.name, restore_values)
            database_label = next(
                label
                for value, label in response.context["restore_form"].fields["backup_id"].choices
                if value == database_file.name
            )
            self.assertIn("S.N 2", database_label)

    def test_media_restore_extracts_uploaded_archive_into_media_root(self):
        with TemporaryDirectory() as work_dir:
            work_path = Path(work_dir)
            backup_root = work_path / "backups"
            media_root = work_path / "media"
            backup_root.mkdir()
            archive_name = "tms_media_backup_uploaded_test.zip"
            with zipfile.ZipFile(backup_root / archive_name, "w") as archive:
                archive.writestr("tenant_photos/restored.txt", "restored media")

            config = {
                "backup_root": str(backup_root),
                "enable_media_backup": True,
            }
            with override_settings(MEDIA_ROOT=media_root), patch(
                "core.views.load_backup_settings",
                return_value=config,
            ):
                response = self.client.post(
                    reverse("core:backup_center"),
                    {
                        "action": "restore_smart",
                        "backup_id": archive_name,
                        "confirm_text": "RESTORE",
                    },
                )

            restored = media_root / "tenant_photos" / "restored.txt"
            self.assertTrue(restored.exists())
            self.assertEqual(restored.read_text(encoding="utf-8"), "restored media")
            self.assertRedirects(response, settings.LOGIN_URL, fetch_redirect_response=False)

    @patch("core.views.restore_database")
    @patch("core.views.create_db_backup", return_value=Path("safety.sql"))
    @patch("core.views.load_backup_settings", return_value={})
    @patch("core.views.list_backups")
    def test_smart_restore_routes_database_backup_correctly(
        self,
        list_backups,
        _load_settings,
        create_backup,
        restore_database,
    ):
        backup = BackupItem(
            id="tms_db_backup_test.sql",
            name="tms_db_backup_test",
            backup_type="db",
            status="success",
            size=10,
            created_at=datetime.now(),
            display_path=__file__,
            storage_mode="sql",
        )
        list_backups.return_value = [backup]

        response = self.client.post(
            reverse("core:backup_center"),
            {
                "action": "restore_smart",
                "backup_id": backup.id,
                "confirm_text": "RESTORE",
            },
        )

        create_backup.assert_called_once_with({"enable_db_backup": True})
        restore_database.assert_called_once_with({}, backup.id)
        self.assertRedirects(response, settings.LOGIN_URL, fetch_redirect_response=False)

    @patch("core.views.restore_full")
    @patch("core.views.create_full_backup", return_value=Path("safety.zip"))
    @patch("core.views.load_backup_settings", return_value={})
    @patch("core.views.list_backups")
    def test_smart_restore_routes_full_backup_correctly(
        self,
        list_backups,
        _load_settings,
        create_backup,
        restore_full,
    ):
        backup = BackupItem(
            id="tms_full_backup_test.zip",
            name="tms_full_backup_test",
            backup_type="full",
            status="success",
            size=10,
            created_at=datetime.now(),
            display_path=__file__,
        )
        list_backups.return_value = [backup]

        response = self.client.post(
            reverse("core:backup_center"),
            {
                "action": "restore_smart",
                "backup_id": backup.id,
                "confirm_text": "RESTORE",
            },
        )

        create_backup.assert_called_once_with({"enable_full_backup": True})
        restore_full.assert_called_once_with({}, backup.id)
        self.assertRedirects(response, settings.LOGIN_URL, fetch_redirect_response=False)

    @patch("core.views.restore_database")
    @patch("core.views.create_db_backup", return_value=Path("safety.sql"))
    @patch("core.views.load_backup_settings", return_value={})
    @patch("core.views.list_backups")
    def test_successful_database_restore_logs_out_user(
        self,
        list_backups,
        _load_settings,
        _create_backup,
        restore_database,
    ):
        list_backups.return_value = [
            BackupItem(
                id="tms_db_backup_test.sql",
                name="tms_db_backup_test",
                backup_type="db",
                status="success",
                size=10,
                created_at=datetime.now(),
                display_path="tms_db_backup_test.sql",
                storage_mode="sql",
            )
        ]

        response = self.client.post(
            reverse("core:backup_center"),
            {
                "action": "restore_db",
                "backup_id": "tms_db_backup_test.sql",
                "confirm_text": "RESTORE DB",
            },
        )

        self.assertRedirects(response, settings.LOGIN_URL, fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        restore_database.assert_called_once_with({}, "tms_db_backup_test.sql")

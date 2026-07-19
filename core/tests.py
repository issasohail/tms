import subprocess
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from core.backup_utils import (
    BackupItem,
    _mysql_client_environment,
    _mysqldump_compatibility_args,
    _run_mysql_command,
)
from core.views import _pending_approval_filter_state


class PendingApprovalFilterTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_defaults_to_pending_status_and_all_dates(self):
        filters = _pending_approval_filter_state(self.factory.get("/pending-approvals/"))

        self.assertEqual(filters["status"], "pending")
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


class BackupMySQLCommandTests(SimpleTestCase):
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


class BackupRestoreViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="backup-admin",
            email="backup@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)

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

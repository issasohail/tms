import subprocess
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

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

    def test_property_unit_and_text_search_are_normalized(self):
        request = self.factory.get(
            "/pending-approvals/",
            {"property": "12", "unit": "34", "q": "  receipt  "},
        )

        filters = _pending_approval_filter_state(request)

        self.assertEqual(filters["property_id"], 12)
        self.assertEqual(filters["unit_id"], 34)
        self.assertEqual(filters["search"], "receipt")


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

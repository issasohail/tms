import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.pending_approval_queue import (
    pending_approval_actionable_counts,
    pending_approval_count,
)
from leases.models import Lease
from properties.models import Property, Unit
from tenants.models import Tenant
from whatsapp.models import PendingWhatsAppMedia


class PendingApprovalBadgeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "pending-badge-admin",
            email="pending-badge@example.com",
            password="test",
        )
        self.client.force_login(self.user)
        self.property = Property.objects.create(
            property_name="Pending Badge Property",
            owner_name="Owner",
            owner_cnic="37405-7654321-1",
            type="Residential",
            property_type="apartment",
            total_units=1,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="PB-01",
            status="occupied",
        )
        self.tenant = Tenant.objects.create(
            first_name="Pending",
            last_name="Tenant",
            phone="+923001234567",
            cnic="37405-1234567-1",
        )

    def _pending_lease(self):
        return Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=timezone.localdate() - timedelta(days=30),
            end_date=timezone.localdate() + timedelta(days=335),
            monthly_rent=Decimal("25000"),
            status="pending_approval",
        )

    def _pending_media(self, filename, *, batch_key=None):
        return PendingWhatsAppMedia.objects.create(
            phone=self.tenant.phone,
            file=f"whatsapp/pending/{filename}",
            original_filename=filename,
            media_type="image/jpeg",
            purpose=PendingWhatsAppMedia.PURPOSE_OTHER,
            batch_key=batch_key,
            tenant=self.tenant,
            lease=None,
            property=self.property,
            unit=self.unit,
        )

    def _ajax_post(self, name, kind, pk):
        return self.client.post(
            reverse(name, args=[kind, pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

    def test_badge_count_equals_default_page_count(self):
        self._pending_lease()
        batch_key = uuid.uuid4()
        self._pending_media("batch-1.jpg", batch_key=batch_key)
        self._pending_media("batch-2.jpg", batch_key=batch_key)

        response = self.client.get(reverse("core:pending_approvals"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["visible_approval_count"], pending_approval_count())
        self.assertEqual(
            response.context["PENDING_APPROVAL_COUNT"],
            response.context["visible_approval_count"],
        )

    def test_approving_item_immediately_decreases_count_and_ajax_returns_it(self):
        lease = self._pending_lease()
        before = pending_approval_count()

        response = self._ajax_post(
            "core:pending_approval_approve", "lease", lease.pk
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pending_approval_count"], before - 1)
        self.assertEqual(pending_approval_count(), before - 1)
        lease.refresh_from_db()
        self.assertEqual(lease.status, "active")

    def test_rejecting_grouped_media_immediately_decreases_actionable_count(self):
        batch_key = uuid.uuid4()
        first = self._pending_media("reject-1.jpg", batch_key=batch_key)
        second = self._pending_media("reject-2.jpg", batch_key=batch_key)
        before = pending_approval_count()

        response = self._ajax_post(
            "core:pending_approval_reject", "media", first.pk
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pending_approval_count"], before - 1)
        self.assertEqual(pending_approval_count(), before - 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, PendingWhatsAppMedia.STATUS_REJECTED)
        self.assertEqual(second.status, PendingWhatsAppMedia.STATUS_REJECTED)

    def test_grouped_media_counts_as_one_actionable_batch(self):
        batch_key = uuid.uuid4()
        self._pending_media("batch-1.jpg", batch_key=batch_key)
        self._pending_media("batch-2.jpg", batch_key=batch_key)
        self._pending_media("batch-3.jpg", batch_key=batch_key)
        self._pending_media("standalone.jpg")

        response = self.client.get(reverse("core:pending_approvals"))
        media_section = next(
            section
            for section in response.context["sections"]
            if section["kind"] == "media"
        )

        self.assertEqual(pending_approval_actionable_counts()["media"], 2)
        self.assertEqual(media_section["count"], 2)
        self.assertEqual(len(media_section["items"]), 2)
        self.assertEqual(response.context["PENDING_APPROVAL_COUNT"], 2)

    def test_empty_queue_has_zero_count_and_hidden_navbar_badges(self):
        response = self.client.get(reverse("core:pending_approvals"))
        html = response.content.decode()

        self.assertEqual(response.context["visible_approval_count"], 0)
        self.assertEqual(response.context["PENDING_APPROVAL_COUNT"], 0)
        self.assertEqual(pending_approval_count(), 0)
        self.assertEqual(html.count("data-pending-approval-count>"), 4)
        self.assertEqual(
            html.count('d-none" data-pending-approval-count>0</span>'),
            4,
        )

    def test_default_page_reuses_each_pending_count_for_navbar_and_sections(self):
        self._pending_lease()
        self._pending_media("standalone.jpg")

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("core:pending_approvals"))

        self.assertEqual(response.status_code, 200)
        count_sql = [
            query["sql"].lower()
            for query in queries.captured_queries
            if query["sql"].lstrip().lower().startswith("select count")
        ]
        pending_tables = (
            "leases_lease",
            "leases_pendingagreementapproval",
            "whatsapp_pendingwhatsapppayment",
            "whatsapp_pendingwhatsappmedia",
            "whatsapp_pendingwhatsappmaintenance",
            "leases_pendingleasefamilymembersubmission",
            "leases_pendingpoliceverificationsubmission",
            "tenants_tenantregistrationsubmission",
        )
        for table_name in pending_tables:
            matching = [
                sql for sql in count_sql if f"from `{table_name}` where" in sql
            ]
            self.assertLessEqual(
                len(matching),
                1,
                f"{table_name} pending count was executed more than once",
            )

        media_count_sql = next(
            sql for sql in count_sql if "whatsapp_pendingwhatsappmedia" in sql
        )
        self.assertNotIn("from (select distinct", media_count_sql)

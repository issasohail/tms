from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase
from django.utils import timezone

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from pypdf import PdfReader, PdfWriter

from leases.services.estamp import (
    authorize_estamp,
    estamp_status,
    latest_estamp,
    normalize_estamp_pdf,
)


class EStampPolicyTests(SimpleTestCase):
    def _lease_with(self, document):
        queryset = Mock()
        queryset.order_by.return_value.first.return_value = document
        manager = Mock()
        manager.filter.return_value = queryset
        return SimpleNamespace(documents=manager), queryset

    def test_latest_estamp_uses_required_ordering(self):
        document = object()
        lease, queryset = self._lease_with(document)
        self.assertIs(latest_estamp(lease), document)
        lease.documents.filter.assert_called_once_with(
            category="estamp_paper", is_active=True
        )
        queryset.order_by.assert_called_once_with("-uploaded_at", "-pk")

    def test_zero_max_age_disables_restriction(self):
        document = SimpleNamespace(uploaded_at=timezone.now() - timedelta(days=400))
        lease, _ = self._lease_with(document)
        status = estamp_status(
            lease,
            config=SimpleNamespace(estamp_max_age_days=0),
        )
        self.assertFalse(status.is_over_age)

    @patch("leases.services.estamp.estamp_status")
    def test_url_flag_cannot_bypass_age_without_permission(self, status_mock):
        status_mock.return_value = SimpleNamespace(
            document=object(), is_over_age=True, can_override=False
        )
        with self.assertRaises(PermissionDenied):
            authorize_estamp(object(), Mock(), allow_over_age=True)


class EStampUploadTests(SimpleTestCase):
    def _pdf(self, password=None):
        from io import BytesIO

        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.add_blank_page(width=612, height=792)
        if password:
            writer.encrypt(password)
        output = BytesIO()
        writer.write(output)
        return SimpleUploadedFile("stamp.pdf", output.getvalue())

    def test_encrypted_pdf_is_saved_unlocked_with_all_pages(self):
        normalized = normalize_estamp_pdf(self._pdf("secret"), "secret")
        reader = PdfReader(normalized)
        self.assertFalse(reader.is_encrypted)
        self.assertEqual(len(reader.pages), 2)

    def test_encrypted_pdf_requires_password(self):
        with self.assertRaisesMessage(ValidationError, "password protected"):
            normalize_estamp_pdf(self._pdf("secret"))

    def test_wrong_password_is_reported_without_exposing_value(self):
        with self.assertRaises(ValidationError) as caught:
            normalize_estamp_pdf(self._pdf("secret"), "do-not-log-me")
        self.assertNotIn("do-not-log-me", str(caught.exception))

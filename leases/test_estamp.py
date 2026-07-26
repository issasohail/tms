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
    compose_stamped_agreement,
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


class EStampCompositionTests(SimpleTestCase):
    def _text_pdf(self, labels, size):
        from io import BytesIO
        from reportlab.pdfgen import canvas

        output = BytesIO()
        pdf = canvas.Canvas(output, pagesize=size)
        for label in labels:
            pdf.drawString(40, size[1] - 40, label)
            pdf.showPage()
        pdf.save()
        return output.getvalue()

    def test_two_stamp_pages_map_only_to_first_two_agreement_pages(self):
        agreement = self._text_pdf(
            ["AGREEMENT ONE", "AGREEMENT TWO", "AGREEMENT THREE"], (612, 1008)
        )
        stamp = self._text_pdf(["STAMP ONE", "STAMP TWO"], (595, 842))
        from io import BytesIO
        result = PdfReader(BytesIO(compose_stamped_agreement(agreement, stamp, "legal")))
        self.assertEqual(len(result.pages), 3)
        self.assertIn("STAMP ONE", result.pages[0].extract_text())
        self.assertIn("STAMP TWO", result.pages[1].extract_text())
        self.assertNotIn("STAMP", result.pages[2].extract_text())
        for page in result.pages:
            self.assertEqual((float(page.mediabox.width), float(page.mediabox.height)), (612, 1008))

    def test_one_stamp_page_is_not_repeated(self):
        agreement = self._text_pdf(["ONE", "TWO"], (612, 792))
        stamp = self._text_pdf(["ONLY STAMP"], (595, 842))
        from io import BytesIO
        result = PdfReader(BytesIO(compose_stamped_agreement(agreement, stamp, "letter")))
        self.assertIn("ONLY STAMP", result.pages[0].extract_text())
        self.assertNotIn("ONLY STAMP", result.pages[1].extract_text())
        self.assertEqual(float(result.pages[0].mediabox.height), 792)

    def test_rotated_non_zero_cropbox_is_normalized_without_mutating_input(self):
        from io import BytesIO
        from pypdf.generic import RectangleObject

        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=500)
        page.cropbox = RectangleObject((20, 30, 280, 470))
        page.rotate(90)
        source = BytesIO()
        writer.write(source)
        stamp = source.getvalue()
        agreement = self._text_pdf(["AGREEMENT"], (612, 792))
        result = compose_stamped_agreement(agreement, stamp, "letter")
        self.assertEqual(stamp, source.getvalue())
        page = PdfReader(BytesIO(result)).pages[0]
        self.assertEqual((float(page.mediabox.width), float(page.mediabox.height)), (612, 792))


class EStampPackageIntegrationTests(SimpleTestCase):
    @patch("leases.services.agreement_package.merge_pdfs", return_value=b"package")
    @patch("leases.services.agreement_package.signature_pdf", return_value=b"signature")
    @patch("leases.services.agreement_package.police_pdf", return_value=b"police")
    @patch("leases.services.agreement_package.inspection_pdf", return_value=b"inspection")
    @patch("leases.services.estamp.compose_stamped_agreement", return_value=b"stamped-core")
    @patch("leases.services.estamp.authorize_estamp")
    @patch("leases.services.agreement_package.agreement_pdf", return_value=b"plain-core")
    def test_only_core_agreement_is_stamped(
        self,
        agreement_mock,
        authorize_mock,
        compose_mock,
        inspection_mock,
        police_mock,
        signature_mock,
        merge_mock,
    ):
        from leases.services.agreement_package import build_package

        file_handle = Mock()
        file_handle.__enter__ = Mock(return_value=SimpleNamespace(read=Mock(return_value=b"stamp")))
        file_handle.__exit__ = Mock(return_value=False)
        authorize_mock.return_value = SimpleNamespace(
            file=SimpleNamespace(open=Mock(return_value=file_handle))
        )
        history = SimpleNamespace(
            print_with_estamp=True,
            estamp_paper_size="letter",
            allow_over_age_estamp=False,
        )
        request = SimpleNamespace(user=Mock())
        lease = Mock()

        payload, _, _ = build_package(request, lease, history, [])

        self.assertEqual(payload, b"package")
        compose_mock.assert_called_once_with(b"plain-core", b"stamp", "letter")
        self.assertEqual(
            merge_mock.call_args.args[0],
            [b"stamped-core", b"inspection", b"police", b"signature"],
        )

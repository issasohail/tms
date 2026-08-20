from datetime import date, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from PIL import Image
from pypdf import PdfReader

from leases.models import Lease
from leases.models_lease_photos import LeaseMedia
from leases.models_renewal import LeaseRenewal
from leases.services.lease_history import ensure_original_history
from properties.models import Property, Unit
from tenants.models import Tenant


class LeasePhotoSettingsTestBase(TestCase):
    def setUp(self):
        self.property = Property.objects.create(
            property_name="Photo Annexure Property",
            owner_name="Owner",
            owner_cnic="61101-1111111-1",
            type="Residential",
            property_type="house",
            total_units=2,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="A-1",
        )
        self.tenant = Tenant.objects.create(
            first_name="Photo",
            last_name="Tenant",
            cnic="61101-2222222-2",
        )
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_rent=20000,
        )
        self.history = ensure_original_history(self.lease)
        self.renewal = LeaseRenewal.objects.create(
            lease=self.lease,
            renewal_number=2,
            start_date=date(2027, 1, 1),
            end_date=date(2027, 12, 31),
            monthly_rent=22000,
        )
        self.user = get_user_model().objects.create_user(
            username="photo-editor",
            email="photo-editor@example.com",
            password="test-password",
        )
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="leases",
                codename="change_lease",
            ),
            Permission.objects.get(
                content_type__app_label="accounts",
                codename="access_all_properties",
            ),
        )

    def media(self, *, lease=None, history=None, media_type="image", active=True):
        row = LeaseMedia(
            lease=lease or self.lease,
            lease_history=history,
            file=f"lease/test-{LeaseMedia.objects.count() + 1}.jpg",
            media_type=media_type,
            is_active=active,
        )
        LeaseMedia.objects.bulk_create([row])
        return LeaseMedia.objects.latest("pk")

    def save_settings(self, history=None, **overrides):
        target = history or self.history
        payload = {
            "action": "save_photo_settings",
            "history_id": target.pk,
            "include_photos": "1",
            "layout": "4up",
            "selection_mode": "selected",
            "photo_ids_submitted": "1",
        }
        payload.update(overrides)
        payload = {key: value for key, value in payload.items() if value is not None}
        return self.client.post(
            reverse("leases:edit_clauses", args=[self.lease.pk]),
            payload,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )


class LeasePhotoSettingsModelTests(LeasePhotoSettingsTestBase):
    def test_defaults_persist_and_are_independent_per_history(self):
        self.assertFalse(self.history.include_lease_photos)
        self.assertEqual(self.history.lease_photo_layout, "4up")
        self.assertEqual(self.history.lease_photo_selection_mode, "selected")
        self.assertEqual(self.history.lease_photo_ids, [])

        self.history.include_lease_photos = True
        self.history.lease_photo_layout = "2up"
        self.history.lease_photo_ids = [10, 20]
        self.history.save()
        self.history.refresh_from_db()
        self.renewal.refresh_from_db()

        self.assertTrue(self.history.include_lease_photos)
        self.assertEqual(self.history.lease_photo_layout, "2up")
        self.assertEqual(self.history.lease_photo_ids, [10, 20])
        self.assertFalse(self.renewal.include_lease_photos)
        self.assertEqual(self.renewal.lease_photo_ids, [])

    def test_renewal_form_does_not_expose_photo_settings(self):
        from leases.forms_renewal import LeaseRenewalForm

        for field in (
            "include_lease_photos",
            "lease_photo_layout",
            "lease_photo_selection_mode",
            "lease_photo_ids",
        ):
            self.assertNotIn(field, LeaseRenewalForm.base_fields)


class LeasePhotoSettingsAjaxTests(LeasePhotoSettingsTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_saves_only_general_and_current_history_images(self):
        general = self.media()
        current = self.media(history=self.history)
        other_history = self.media(history=self.renewal)
        inactive = self.media(active=False)
        video = self.media(media_type="video")

        response = self.save_settings(
            photo_ids=[
                general.pk,
                current.pk,
                other_history.pk,
                inactive.pk,
                video.pk,
                999999,
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.history.refresh_from_db()
        self.assertEqual(self.history.lease_photo_ids, [general.pk, current.pk])
        self.assertEqual(
            response.json()["settings"]["selected_photo_ids"],
            [general.pk, current.pk],
        )
        self.assertEqual(response.json()["settings"]["eligible_count"], 2)

    def test_other_lease_photo_and_history_are_rejected_by_scope(self):
        other_property = Property.objects.create(
            property_name="Other",
            owner_name="Other Owner",
            owner_cnic="61101-3333333-3",
            type="Residential",
            property_type="house",
            total_units=1,
        )
        other_unit = Unit.objects.create(property=other_property, unit_number="B-1")
        other_tenant = Tenant.objects.create(
            first_name="Other",
            last_name="Tenant",
            cnic="61101-4444444-4",
        )
        other_lease = Lease.objects.create(
            tenant=other_tenant,
            unit=other_unit,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_rent=10000,
        )
        foreign_photo = self.media(lease=other_lease)
        foreign_history = ensure_original_history(other_lease)

        response = self.save_settings(photo_ids=[foreign_photo.pk])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["settings"]["selected_photo_ids"], [])

        response = self.save_settings(
            history=foreign_history,
            photo_ids_submitted="1",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "error")

    def test_turning_off_preserves_selection_and_resave_cleans_stale_ids(self):
        photo = self.media()
        self.save_settings(photo_ids=[photo.pk])
        response = self.save_settings(
            include_photos="0",
            photo_ids_submitted=None,
        )
        self.assertEqual(response.status_code, 200)
        self.history.refresh_from_db()
        self.assertFalse(self.history.include_lease_photos)
        self.assertEqual(self.history.lease_photo_ids, [photo.pk])

        LeaseMedia.objects.filter(pk=photo.pk).update(is_active=False)
        response = self.save_settings(
            include_photos="1",
            photo_ids_submitted=None,
        )
        self.assertEqual(response.status_code, 200)
        self.history.refresh_from_db()
        self.assertEqual(self.history.lease_photo_ids, [])

    def test_invalid_layout_and_mode_return_json_400(self):
        response = self.save_settings(layout="9up")
        self.assertEqual(response.status_code, 400)
        self.assertIn("layout", response.json()["message"])

        response = self.save_settings(selection_mode="foreign")
        self.assertEqual(response.status_code, 400)
        self.assertIn("selection mode", response.json()["message"])

    def test_authentication_and_permission_are_required(self):
        self.client.logout()
        response = self.save_settings()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["status"], "error")

        user = get_user_model().objects.create_user(
            username="photo-no-permission",
            email="photo-no-permission@example.com",
            password="x",
        )
        self.client.force_login(user)
        response = self.save_settings()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["status"], "error")


class LeasePhotoPackageTests(SimpleTestCase):
    @patch("leases.services.agreement_package.merge_pdfs", return_value=b"merged")
    @patch("leases.services.agreement_package.photo_annexure_pdf", return_value=b"photos")
    @patch("leases.services.agreement_package.signature_pdf", return_value=b"signature")
    @patch("leases.services.agreement_package.police_pdf", return_value=b"police")
    @patch("leases.services.agreement_package.inspection_pdf", return_value=b"inspection")
    @patch("leases.services.agreement_package.identity_pdf", return_value=b"identity")
    @patch("leases.services.agreement_package.agreement_pdf", return_value=b"agreement")
    def test_photo_annexure_is_appended_last(
        self,
        _agreement,
        _identity,
        _inspection,
        _police,
        _signature,
        _photos,
        merge,
    ):
        from leases.services.agreement_package import build_package

        history = SimpleNamespace(
            include_lease_photos=True,
            print_with_estamp=False,
            estamp_paper_size="letter",
        )
        lease = Mock(pk=1)
        lease.tenant.get_full_name.return_value = "Tenant"
        lease.unit.property.property_name = "Property"
        lease.unit.unit_number = "Unit"
        lease.start_date = date(2026, 1, 1)
        lease.end_date = date(2026, 12, 31)

        payload, _filename, _ = build_package(
            SimpleNamespace(user=Mock()),
            lease,
            history,
            [],
        )

        self.assertEqual(payload, b"merged")
        self.assertEqual(
            merge.call_args.args[0],
            [
                b"agreement",
                b"identity",
                b"inspection",
                b"police",
                b"signature",
                b"photos",
            ],
        )

    @patch("leases.services.agreement_package.merge_pdfs", return_value=b"merged")
    @patch(
        "leases.services.agreement_package.photo_annexure_pdf",
        side_effect=OSError("optional photo failure"),
    )
    @patch("leases.services.agreement_package.signature_pdf", return_value=b"signature")
    @patch("leases.services.agreement_package.police_pdf", return_value=b"police")
    @patch("leases.services.agreement_package.inspection_pdf", return_value=b"inspection")
    @patch("leases.services.agreement_package.identity_pdf", return_value=b"identity")
    @patch("leases.services.agreement_package.agreement_pdf", return_value=b"agreement")
    def test_optional_photo_failure_does_not_abort_package(
        self,
        _agreement,
        _identity,
        _inspection,
        _police,
        _signature,
        _photos,
        merge,
    ):
        from leases.services.agreement_package import build_package

        history = SimpleNamespace(
            pk=2,
            include_lease_photos=True,
            print_with_estamp=False,
            estamp_paper_size="letter",
        )
        lease = Mock(pk=1)
        lease.tenant.get_full_name.return_value = "Tenant"
        lease.unit.property.property_name = "Property"
        lease.unit.unit_number = "Unit"
        lease.start_date = date(2026, 1, 1)
        lease.end_date = date(2026, 12, 31)

        payload, _filename, _ = build_package(
            SimpleNamespace(user=Mock()),
            lease,
            history,
            [],
        )

        self.assertEqual(payload, b"merged")
        self.assertEqual(
            merge.call_args.args[0],
            [b"agreement", b"identity", b"inspection", b"police", b"signature"],
        )


class LeasePhotoBurnTypographyTests(SimpleTestCase):
    def test_burn_text_uses_readable_agreement_scale(self):
        from leases.models_lease_photos import (
            STAMP_DESC_SCALE,
            STAMP_MIN_PX,
            STAMP_TS_SCALE,
        )

        self.assertGreaterEqual(STAMP_TS_SCALE, 1.20)
        self.assertGreaterEqual(STAMP_DESC_SCALE, 1.20)
        self.assertGreaterEqual(STAMP_MIN_PX, 22)


class LeasePhotoUploadModalTemplateTests(SimpleTestCase):
    def test_successful_upload_closes_modal_when_batch_finishes(self):
        from django.template.loader import get_template

        source = get_template("leases/photos_page.html").template.source

        self.assertIn("if (!failures.length)", source)
        self.assertIn("modal.classList.remove('is-open')", source)
        self.assertNotIn("__leasePhotoUploadCloseTimer", source)


class LeasePhotoExporterTests(SimpleTestCase):
    def image_bytes(self):
        out = BytesIO()
        Image.new("RGB", (800, 600), "white").save(out, "JPEG")
        return out.getvalue()

    def lease(self):
        tenant = SimpleNamespace(get_full_name=lambda: "Photo Tenant")
        prop = SimpleNamespace(property_name="Photo Property")
        unit = SimpleNamespace(property=prop, unit_number="A-1")
        return SimpleNamespace(
            pk=1,
            tenant=tenant,
            unit=unit,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )

    def media(self, count):
        return [
            SimpleNamespace(
                pk=index,
                file=SimpleNamespace(name=f"photo-{index}.jpg"),
                title=f"Photo {index}",
                description="Move-in condition",
            )
            for index in range(1, count + 1)
        ]

    @patch("leases.services.export_lease_photos_pdf.default_storage")
    def test_embedded_mode_suppresses_standalone_number_and_keeps_safe_footer(self, storage):
        from leases.services.export_lease_photos_pdf import export_lease_photos_pdf

        storage.exists.return_value = True
        storage.open.side_effect = lambda *_args, **_kwargs: BytesIO(
            self.image_bytes()
        )
        media = self.media(1)
        history = SimpleNamespace(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            history_label="Original Lease",
        )

        _name, embedded = export_lease_photos_pdf(
            self.lease(),
            layout="4up",
            photos_qs=media,
            package_mode=True,
            history=history,
        )
        embedded_reader = PdfReader(BytesIO(embedded.read()))
        embedded_text = embedded_reader.pages[0].extract_text()
        self.assertIn("ANNEXURE - LEASE CONDITION PHOTOGRAPHS", embedded_text)
        self.assertNotIn("Page 1/1", embedded_text)
        self.assertIn("Tenant Signature", embedded_text)

        _name, standalone = export_lease_photos_pdf(
            self.lease(),
            layout="4up",
            photos_qs=media,
        )
        standalone_text = PdfReader(BytesIO(standalone.read())).pages[0].extract_text()
        self.assertIn("Page 1/1", standalone_text)

    @patch("leases.services.export_lease_photos_pdf.default_storage")
    def test_all_layouts_use_expected_page_count(self, storage):
        from leases.services.export_lease_photos_pdf import export_lease_photos_pdf

        storage.exists.return_value = True
        storage.open.side_effect = lambda *_args, **_kwargs: BytesIO(
            self.image_bytes()
        )
        for layout, expected_pages in (("4up", 2), ("2up", 3), ("1up", 5)):
            _name, output = export_lease_photos_pdf(
                self.lease(),
                layout=layout,
                photos_qs=self.media(5),
                package_mode=True,
            )
            self.assertEqual(
                len(PdfReader(BytesIO(output.read())).pages),
                expected_pages,
            )

    @patch("leases.services.export_lease_photos_pdf.default_storage")
    def test_corrupted_photos_are_skipped_and_all_corrupt_returns_empty(self, storage):
        from leases.services.export_lease_photos_pdf import export_lease_photos_pdf

        storage.exists.return_value = True

        def open_photo(name, *_args, **_kwargs):
            return BytesIO(
                b"not-an-image" if "photo-1" in name else self.image_bytes()
            )

        storage.open.side_effect = open_photo
        _name, output = export_lease_photos_pdf(
            self.lease(),
            photos_qs=self.media(2),
            package_mode=True,
        )
        text = PdfReader(BytesIO(output.read())).pages[0].extract_text()
        self.assertIn("Total photographs: 1", text)

        storage.open.side_effect = lambda *_args, **_kwargs: BytesIO(b"bad")
        self.assertEqual(
            export_lease_photos_pdf(
                self.lease(),
                photos_qs=self.media(1),
                package_mode=True,
            ),
            (None, None),
        )

    @patch("leases.services.agreement_package.AgreementSignatureTemplate.current")
    @patch("leases.services.export_lease_photos_pdf.default_storage")
    def test_final_merger_numbers_photo_page_without_footer_collision(
        self,
        storage,
        config,
    ):
        from reportlab.pdfgen import canvas
        from leases.services.agreement_package import merge_pdfs
        from leases.services.export_lease_photos_pdf import export_lease_photos_pdf

        storage.exists.return_value = True
        storage.open.side_effect = lambda *_args, **_kwargs: BytesIO(
            self.image_bytes()
        )
        config.return_value = SimpleNamespace(
            agreement_letter_footer_bottom_points=16,
            show_agreement_page_numbers=True,
        )
        _name, annexure = export_lease_photos_pdf(
            self.lease(),
            photos_qs=self.media(1),
            package_mode=True,
        )
        first = BytesIO()
        pdf = canvas.Canvas(first, pagesize=(612, 792))
        pdf.drawString(50, 740, "AGREEMENT")
        pdf.save()

        merged = merge_pdfs([first.getvalue(), annexure.read()])
        reader = PdfReader(BytesIO(merged))
        self.assertIn("Page 1 of 2", reader.pages[0].extract_text())
        last_text = reader.pages[-1].extract_text()
        self.assertIn("Page 2 of 2", last_text)
        self.assertNotIn("Page 1/1", last_text)

        import fitz

        page = fitz.open(stream=merged, filetype="pdf")[-1]
        signature_rect = page.search_for("Tenant Signature")[0]
        page_number_rect = page.search_for("Page 2 of 2")[0]
        self.assertFalse(signature_rect.intersects(page_number_rect))

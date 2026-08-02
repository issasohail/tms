from io import BytesIO
from pathlib import Path

from django.test import SimpleTestCase
from PIL import Image

from tenants.services.cnic_ocr import _auto_orient_cnic_source


class CNICAutoOrientationTests(SimpleTestCase):
    def _jpeg(self, size):
        output = BytesIO()
        Image.new("RGB", size, "white").save(output, format="JPEG")
        return output.getvalue()

    def test_portrait_scan_is_rotated_to_landscape(self):
        source = self._jpeg((400, 650))

        oriented, was_rotated = _auto_orient_cnic_source(source)

        with Image.open(BytesIO(oriented)) as image:
            self.assertGreater(image.width, image.height)
        self.assertTrue(was_rotated)

    def test_landscape_scan_is_not_reencoded(self):
        source = self._jpeg((650, 400))

        oriented, was_rotated = _auto_orient_cnic_source(source)

        self.assertEqual(oriented, source)
        self.assertFalse(was_rotated)


class CNICRegistrationTemplateCoverageTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        project_root = Path(__file__).resolve().parent.parent
        cls.identity_source = (
            project_root / "templates" / "partials" / "identity_media_public.html"
        ).read_text(encoding="utf-8")
        cls.public_source = (
            project_root
            / "tenants"
            / "templates"
            / "tenants"
            / "public_registration_form.html"
        ).read_text(encoding="utf-8")

    def test_ocr_installs_for_party_roles_and_family_members(self):
        self.assertIn(".family-member-card,.party-role-card", self.identity_source)
        self.assertIn("endsWith('cnic_front')", self.identity_source)

    def test_public_registration_allows_manual_correction_after_ocr_error(self):
        self.assertIn("TMS_CNIC_ALLOW_MANUAL_ENTRY = true", self.public_source)
        self.assertIn(
            "const allowManualEntry=window.TMS_CNIC_ALLOW_MANUAL_ENTRY===true;",
            self.identity_source,
        )

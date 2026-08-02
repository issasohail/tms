from io import BytesIO
from pathlib import Path

from django.test import SimpleTestCase
from PIL import Image, ImageDraw

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

    def _back_with_qr_pattern(self, upside_down=False):
        image = Image.new("RGB", (800, 500), "white")
        draw = ImageDraw.Draw(image)
        if upside_down:
            origin_x, origin_y = 35, 300
        else:
            origin_x, origin_y = 570, 30
        cell = 14
        for row in range(12):
            for column in range(12):
                if (row + column) % 2 == 0:
                    draw.rectangle(
                        (
                            origin_x + column * cell,
                            origin_y + row * cell,
                            origin_x + (column + 1) * cell - 1,
                            origin_y + (row + 1) * cell - 1,
                        ),
                        fill="black",
                    )
        output = BytesIO()
        image.save(output, format="JPEG", quality=95)
        return output.getvalue()

    def test_upside_down_back_is_rotated_by_half_turn(self):
        source = self._back_with_qr_pattern(upside_down=True)

        oriented, was_rotated = _auto_orient_cnic_source(source, side="back")

        with Image.open(BytesIO(oriented)) as image:
            top_right = image.crop((550, 0, 800, 220)).convert("L")
            bottom_left = image.crop((0, 280, 250, 500)).convert("L")
            self.assertLess(
                sum(top_right.getdata()) / (top_right.width * top_right.height),
                sum(bottom_left.getdata()) / (bottom_left.width * bottom_left.height),
            )
        self.assertTrue(was_rotated)

    def test_upright_back_is_not_reencoded(self):
        source = self._back_with_qr_pattern(upside_down=False)

        oriented, was_rotated = _auto_orient_cnic_source(source, side="back")

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

    def test_quick_registration_shell_names_are_replaceable_by_ocr(self):
        self.assertIn("TMS_CNIC_REPLACE_SHELL_NAMES", self.public_source)
        self.assertIn("isReplaceableShellName", self.identity_source)
        self.assertIn("value==='new'", self.identity_source)
        self.assertIn("value==='registration'", self.identity_source)

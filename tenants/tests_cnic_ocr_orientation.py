from io import BytesIO

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

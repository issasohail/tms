"""Guard project source against incorrectly decoded text."""

from django.test import SimpleTestCase

from scripts.check_text_encoding import scan_project


class ProjectTextEncodingTests(SimpleTestCase):
    def test_source_text_is_utf8_without_mojibake(self):
        issues = scan_project()
        self.assertEqual(issues, [], "\n".join(issues))

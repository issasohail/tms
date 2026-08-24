from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class LiveCustomResponsivePowerStatusTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        template_path = (
            Path(settings.BASE_DIR)
            / "smart_meter"
            / "templates"
            / "smart_meter"
            / "live_custom.html"
        )
        cls.source = template_path.read_text(encoding="utf-8")

    def test_mobile_and_tablet_card_layout_keeps_serial_number(self):
        self.assertIn("@media (max-width: 991.98px)", self.source)
        self.assertIn('class="sticky-left sno text-center">{{ forloop.counter }}', self.source)
        self.assertIn("tr[data-meter-row] > td.sno", self.source)

    def test_row_has_accessible_power_operation_status_below_tenant(self):
        tenant_end = self.source.index('<span class="power-operation-status"')
        tenant_start = self.source.rfind('<span class="mobile-tenant"', 0, tenant_end)

        self.assertGreater(tenant_start, -1)
        self.assertIn('role="status"', self.source[tenant_end : tenant_end + 140])
        self.assertIn('aria-live="polite"', self.source[tenant_end : tenant_end + 140])

    def test_explicit_relay_action_does_not_overwrite_connectivity_badge(self):
        action_start = self.source.index("async function requestRelayAction(btn)")
        action_end = self.source.index("document.addEventListener('click'", action_start)
        action_source = self.source[action_start:action_end]

        self.assertNotIn("setOnlineBadge", action_source)
        self.assertNotIn("setPowerBadge", action_source)
        self.assertNotIn("dataset.status", action_source)
        self.assertIn("btn.dataset.action", action_source)
        self.assertIn("setRowOperationStatus", action_source)

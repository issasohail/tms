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
        tenant_end = self.source.index(
            '<span class="power-operation-status {{ r.relay_indicator_class }}"'
        )
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

    def test_balance_is_visible_and_refreshed_for_desktop_and_mobile(self):
        self.assertIn('<th data-col="balance">Balance</th>', self.source)
        self.assertIn('class="mobile-balance"', self.source)
        self.assertIn('function setBalanceCell(tr, newValue, flash = true)', self.source)
        self.assertIn('setBalanceCell(tr, r.balance, true);', self.source)
        self.assertNotIn(
            'th[data-col="balance"],\n  td[data-col="balance"] {\n    display: none !important;',
            self.source,
        )

    def test_desktop_status_moves_beside_billing_and_saves_column_space(self):
        badges_start = self.source.index('<div class="desktop-meter-badges">')
        badges_source = self.source[badges_start : badges_start + 600]

        self.assertIn('class="badge bg-success">Billing</span>', badges_source)
        self.assertIn('class="badge online-badge', badges_source)
        self.assertIn('@media (min-width: 992px)', self.source)
        self.assertIn('th.col-status,', self.source)
        self.assertIn('td.col-power > .power-badge', self.source)

    def test_initial_and_polled_operation_states_use_server_reconciliation(self):
        self.assertIn(
            '{{ r.relay_indicator_label }}',
            self.source,
        )
        self.assertIn(
            "reading.relay_operation_label || (desired === 'on' ? 'Restoring…' : 'Connecting…')",
            self.source,
        )
        self.assertIn("'sent', 'retry'].includes(status)", self.source)
        self.assertIn("status === 'acknowledged'", self.source)
        self.assertIn("setRowOperationStatus(tr, '', '');", self.source)

    def test_connectivity_badge_supports_online_stale_and_offline(self):
        self.assertIn('b.textContent = "Online"', self.source)
        self.assertIn('b.textContent = "Data stale"', self.source)
        self.assertIn('b.textContent = "Offline"', self.source)
        self.assertIn('setRowOnlineState(tr, r.connection_state);', self.source)
        self.assertIn("r.connection_state == 'stale'", self.source)

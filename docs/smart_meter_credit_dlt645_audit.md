# Smart meter credit-control and DL/T645 audit

## Scope audited

This note records the live integration points in the uploaded TMS snapshot before credit-control changes.

### Live meter data path

- `smart_meter/management/commands/meter_listener.py` is the live Django TCP listener command.
- It parses frames with `smart_meter.parser.parse_frame`, resolves the meter by `meter_number`, updates the one-row-per-meter `LiveReading`, and periodically appends `MeterReading` historical snapshots.
- `MeterReading.total_energy` / `LiveReading.total_energy` are cumulative kWh fields used by existing billing and reporting.
- The listener also owns the in-process active socket registry (`ACTIVE_HANDLERS`) and the DB command poller.
- Reading persistence must remain independent of credit calculations and tenant notifications.

### Meter installation/history

- `MeterInstallation` is the authoritative assignment history and records meter, unit, lease, start/end dates and start/end readings.
- Active installations are guarded by `active_meter_key`; credit accounts should bind to an installation and lease, not merely a unit.
- `Meter.meter_role` distinguishes billing meters from audit/check meters.

### Existing billing modes

- `Meter.billing_mode` currently has `postpaid` and `prepaid`.
- Migration `0010_meter_billing_mode_meterassignmenthistory.py` marked meters with `MeterPrepaidSettings` as `prepaid`.
- Existing `prepaid` must therefore remain compatible and must not silently become native prepaid pilot.
- Existing postpaid invoice paths explicitly exclude only the legacy `prepaid` value; credit-controlled postpaid must continue to use postpaid billing calculations.

### Existing switching and command lifecycle

- `MeterCommand` already stores queued DL/T645 frames and is processed by the listener's `DbCommandPoller`.
- Existing helper `smart_meter.utils.commands` builds relay frames with `smart_meter.vendor.switch_OnOff.frame_command` and sends through the listener control path.
- Vendor relay operation values currently used are `0x1A` OFF and `0x1C` ON.
- Existing manual controls must be routed through the same durable command lifecycle rather than introducing a second relay implementation.
- Existing `Meter.is_cutoff` uses a documented-in-code heuristic based on `status_word`; it must not become authoritative verification for automatic enforcement.

### Existing prepaid code

- `MeterPrepaidSettings` already stores alarm values, overdraft limit, tariff values and switch times.
- `smart_meter.prepaid.DLT645_2007_Prepaid` builds/parses a large manufacturer-style parameter block.
- `views_prepaid.py` currently writes parameters directly through the listener with retries. This path lacks allowlist, read-before-write, read-back verification and audit controls and should be treated as experimental legacy behavior.

### Existing electricity/tariff/accounting behavior

- `smart_meter.services.invoicing._detect_unit_rate` prefers `Meter.unit_rate` and then `Lease.electric_unit_rate`.
- Existing monthly invoice generation uses historical installation/readings and produces invoice items in the regular invoice/accounting system.
- Invoice outstanding allocation is exposed by `Invoice.outstanding_balance`; security-deposit accounting is handled separately by `invoices.services.security_deposit_totals`.
- Meter credit limits may derive a number from the electricity security deposit but must never consume or mutate the deposit ledger.

## Repository hygiene

Live files were distinguished from stale duplicates. Do not edit files named `* copy.py`, `*.bak*`, `*.backup*`, or generated/static/media data. Notable duplicates include `smart_meter_server copy*.py`, `smart_meter/management/commands/meter_listener copy*.py`, and backup smart-meter views/models/templates.

## Protocol documentation gap in this upload

The implementation prompt references `/mnt/data/tms22(1).zip` and `/mnt/data/meter smart-20260804T112222Z-1-001.zip`. Neither archive is present inside the uploaded `TMS_2026-08-11_04-30-38_0d47887a.zip` snapshot or alongside it in the working upload set used for this patch.

Existing repository code is sufficient to preserve the current DL/T645 relay frame and prepaid parameter framework, but it is **not sufficient evidence to invent** a recharge DI, authoritative relay-status DI/bit map, password/encryption semantics, or replay/purchase sequence behavior. Native recharge and authoritative relay verification must remain disabled until manufacturer documentation explicitly defines those items.

## Integration principles

1. Keep the listener/parser/storage transaction unchanged except for a fail-open, lightweight evaluation-request enqueue after successful storage.
2. Run financial evaluation from a separate service/management command.
3. Reuse `MeterCommand` as the durable command queue.
4. Revalidate deferred automatic commands immediately before dispatch.
5. Keep automatic cutoff/restore and prepaid writes disabled by default through environment switches.
6. Preserve legacy `prepaid` behavior; add new `credit_controlled` and `prepaid_pilot` modes without reinterpreting legacy data.

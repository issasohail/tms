# Bidirectional and three-phase metering

## Register meaning and billing authority

- `00010000` is **Forward Active Energy**, a cumulative kWh register.
- `00020000` is **Reverse Active Energy**, a cumulative kWh register.
- `total_energy` remains the authoritative cumulative register used by existing
  tenant billing and legacy reports. A validated `00010000` reply updates both
  `forward_active_energy_kwh` and `total_energy`; a validated `028011FF`
  `total_energy` value also mirrors into `forward_active_energy_kwh`.
- Reverse active energy never changes `total_energy` and is never subtracted from
  tenant usage. It is used only for grid-interface export reconciliation.
- Net Grid Energy is a reporting calculation: forward/import delta minus
  reverse/export delta. It is not a tenant-billing input.

## Reading profiles

- `auto` preserves the existing meter behavior, including the manufacturer
  `028011FF` bulk response.
- `total_only` allows direct forward/reverse cumulative-register polling.
- `total_and_per_phase` allows the same totals plus the documented phase
  voltage, current, and active-power DIs.

The profile is intentionally one field rather than separate `phase_count` and
`reading_profile` fields. It expresses an acquisition/storage behavior and
avoids invalid combinations such as a three-phase count with a total-only phase
polling policy. Existing meters default to `auto`; migration 0028 selects only
meters `260305510019`, `260305510020`, and `260305510021` by `meter_number`.

## Safe polling

`python manage.py query_energy_registers --meter METER_NUMBER` queries forward
and reverse energy sequentially without persisting them. Use `--all-three` for
the approved three meters, `--include-phases` for meters configured with the
three-phase profile, and `--persist` only when validated replies should update
live/history storage. The listener correlates replies by meter number and DI.

All queries are DL/T645 read commands (`0x11`). This workflow contains no relay,
programming, recharge, or write operation.

## Discontinuities

Reconciliation uses readings nearest both exclusive period boundaries under the
existing 24-hour tolerance tiers. Any decrease within either cumulative register
is treated as an unconfirmed reset/rollover discontinuity, and the affected delta
is withheld. Negative deltas are never silently clamped to zero.

## Manufacturer-specific uncertainty

The supplied captures confirm the address, checksum variant, DI byte order, and
zero-value scaling for meter `260305510020`. Non-zero values and phase DIs are
covered by protocol fixtures, but physical non-zero replies and phase-register
scaling from all three installed meters still require read-only field capture.

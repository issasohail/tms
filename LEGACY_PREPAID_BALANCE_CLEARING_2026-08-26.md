# Legacy Prepaid Balance Clearing Audit — 2026-08-26

## Safety status

- Physical money commands sent by this run: **0**
- Database balance fields modified by this run: **0**
- Historical readings modified or deleted: **0**
- Relay, tariff, firmware, and meter configuration changes: **0**
- Current gate: **BLOCKED BEFORE PILOT** because the tested listener code has not been loaded by the running production service. Both `systemctl restart` and `sudo -n systemctl restart` were denied for lack of interactive authorization.
- Do not begin the pilot until `tms-meter-listener.service` has been restarted and confirmed active with a new PID/start timestamp.

## Authoritative data model and parsing audit

- Physical balance: `smart_meter.LiveReading.balance` (`DecimalField`, 2 decimal places).
- Latest total energy: `smart_meter.LiveReading.total_energy` (3 decimal places).
- Latest total power: `smart_meter.LiveReading.total_power` (3 decimal places).
- Latest parsed reading timestamp: `smart_meter.LiveReading.ts` (`auto_now`; one row per meter).
- Historical energy/power: `smart_meter.MeterReading.total_energy`, `total_power`, and `ts`.
- `MeterReading` has no balance field. Historical physical balances below were recovered from timestamped `parse_frame` records in the production listener logs; they were not inferred from billing data.
- Meter-to-location mapping used the active `MeterInstallation` and its `unit.property`; all audited active installations agreed with the meter's cached `unit`.
- Online/stale threshold: `SMART_METER_ONLINE_THRESHOLD_MINUTES = 10`.

The listener parses only valid `028011FF` responses into `LiveReading`, updates the reading timestamp, and calls prepaid reconciliation with the parsed physical balance. Historical `MeterReading` snapshots retain energy and power but not balance.

## Phase 1 — inventory before

Audit reference time was approximately 2026-08-26 05:47–05:54 (+05:00 listener-local time). Ten meters had `LiveReading.balance > 0`.

| Meter ID | Meter | Property / unit | Latest valid reading (+05:00) | Current balance | Previous balance | Latest energy | Previous energy | Immediate kWh diff | Immediate balance diff | Effective rate | Actively consuming | Classification |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 79 | 241203510006 | F56 Basement / F56-ROOM# 06 | 2026-08-26 05:43:47 | 18.96 | 18.96 | 607.230 | 607.220 | 0.010 | 0.00 | 0.00 Rs/kWh | Yes, 0.117 kW | NOT_DEDUCTING |
| 75 | 241203510007 | F56 Basement / F56-ROOM# 02 | 2026-08-26 05:43:54 | 57.36 | 57.36 | 361.020 | 361.020 | 0.000 | 0.00 | n/a | No, 0.000 kW | INSUFFICIENT_DATA |
| 96 | 241203510001 | F56 Basement / F56-ROOM# 01 | 2026-08-26 05:43:57 | 122.00 | 122.00 | 577.050 | 577.040 | 0.010 | 0.00 | 0.00 Rs/kWh | Yes, 0.064 kW | NOT_DEDUCTING |
| 76 | 250619510004 | F56 Basement / F56-ROOM# 03 | 2026-08-26 05:43:54 | 200.00 | 200.00 | 288.010 | 288.010 | 0.000 | 0.00 | n/a | Yes, 0.116 kW | NOT_DEDUCTING (longer window) |
| 88 | 241203510005 | F56 Basement / F56-ROOM# 10 | 2026-08-26 05:43:48 | 262.96 | 262.96 | 1001.640 | 1001.630 | 0.010 | 0.00 | 0.00 Rs/kWh | Yes, 0.144 kW | NOT_DEDUCTING |
| 100 | 241203510003 | F54 / F54-FLAT# 01 | 2026-08-26 02:33:53 | 370.16 | 370.16 | 1912.200 | 1912.180 | 0.020 | 0.00 | 0.00 Rs/kWh | 0.225 kW at last read | OFFLINE/STALE; history indicates NOT_DEDUCTING |
| 102 | 241203510004 | F56 Basement / F56-ROOM# 05 | 2026-08-26 05:43:48 | 1261.20 | 1261.20 | 623.260 | 623.260 | 0.000 | 0.00 | n/a | Tiny load, 0.005 kW | NOT_DEDUCTING (longer window) |
| 77 | 241203510002 | F56 Basement / F56-ROOM# 04 | 2026-08-26 05:43:51 | 1462.88 | 1462.88 | 401.220 | 401.220 | 0.000 | 0.00 | n/a | Yes, 0.117 kW | NOT_DEDUCTING (longer window) |
| 92 | 241203510010 | F54 / F54-FLAT# 02 | 2026-08-26 05:45:16 | 1765.28 | 1765.28 | 2073.600 | 2073.590 | 0.010 | 0.00 | 0.00 Rs/kWh | Yes, 0.307 kW | NOT_DEDUCTING |
| 94 | 241203510009 | F54 / F54-FLAT# 03 | 2026-08-26 05:43:34 | 4083.04 | 4083.04 | 1513.090 | 1513.090 | 0.000 | 0.00 | n/a | Tiny load, 0.004 kW | NOT_DEDUCTING (longer window) |

### Deduction analysis over meaningful history windows

The calculation is `(previous balance - current balance) / (current energy - previous energy)` only when the energy delta is positive and the balance decrease is non-negative.

| Meter | Window | Energy delta | Balance decrease | Calculation | Conclusion |
|---|---|---:|---:|---|---|
| 241203510006 | 2026-08-25 01:01:22 → 2026-08-26 05:43:47 | 2.05 kWh | 0.00 | 0.00 / 2.05 = **0.00 Rs/kWh** | NOT_DEDUCTING |
| 241203510007 | 2026-08-25 01:01:22 → 2026-08-26 05:43:54 | 0.00 kWh | 0.00 | n/a | INSUFFICIENT_DATA |
| 241203510001 | 2026-08-25 01:01:23 → 2026-08-26 05:43:57 | 1.60 kWh | 0.00 | 0.00 / 1.60 = **0.00 Rs/kWh** | NOT_DEDUCTING |
| 250619510004 | 2026-08-25 01:01:22 → 2026-08-26 05:43:54 | 0.08 kWh | 0.00 | 0.00 / 0.08 = **0.00 Rs/kWh** | NOT_DEDUCTING |
| 241203510005 | 2026-08-26 03:06:12 → 2026-08-26 05:43:48 | 0.43 kWh | 0.00 | 0.00 / 0.43 = **0.00 Rs/kWh** | NOT_DEDUCTING |
| 241203510003 | 2026-08-25 01:01:24 → 2026-08-26 02:33:53 | 6.06 kWh | 0.00 | 0.00 / 6.06 = **0.00 Rs/kWh** | NOT_DEDUCTING while reporting; now stale |
| 241203510004 | 2026-08-25 01:01:24 → 2026-08-26 05:43:48 | 0.15 kWh | 0.00 | 0.00 / 0.15 = **0.00 Rs/kWh** | NOT_DEDUCTING |
| 241203510002 | 2026-08-25 01:01:23 → 2026-08-26 05:43:51 | 3.46 kWh | 0.00 | 0.00 / 3.46 = **0.00 Rs/kWh** | NOT_DEDUCTING |
| 241203510010 | 2026-08-25 01:01:09 → 2026-08-26 05:45:16 | 7.81 kWh | 0.00 | 0.00 / 7.81 = **0.00 Rs/kWh** | NOT_DEDUCTING |
| 241203510009 | 2026-08-25 01:03:13 → 2026-08-26 05:43:34 | 0.13 kWh | 0.00 | 0.00 / 0.13 = **0.00 Rs/kWh** | NOT_DEDUCTING |

Conclusion: the old positive production balances were not decreasing with measured consumption in the available authoritative meter-response history. This does not infer or change any tariff.

### Last five valid physical balance/energy readings

Times below are listener-local (+05:00). Format: `timestamp — balance / total_energy / total_power`.

- **241203510006:** 05:28:06 — 18.96 / 607.200 / 0.1184; 05:30:40 — 18.96 / 607.200 / 0.1171; 05:35:41 — 18.96 / 607.210 / 0.1146; 05:41:15 — 18.96 / 607.220 / 0.1158; 05:43:48 — 18.96 / 607.230 / 0.1166.
- **241203510007:** 05:28:13 — 57.36 / 361.020 / 0.0000; 05:30:46 — 57.36 / 361.020 / 0.0000; 05:35:47 — 57.36 / 361.020 / 0.0000; 05:41:21 — 57.36 / 361.020 / 0.0000; 05:43:54 — 57.36 / 361.020 / 0.0000.
- **241203510001:** 05:28:16 — 122.00 / 577.030 / 0.0660; 05:30:49 — 122.00 / 577.030 / 0.0654; 05:35:50 — 122.00 / 577.040 / 0.0651; 05:41:24 — 122.00 / 577.040 / 0.0642; 05:43:57 — 122.00 / 577.050 / 0.0639.
- **250619510004:** 05:28:13 — 200.00 / 288.000 / 0.0000; 05:30:46 — 200.00 / 288.000 / 0.0000; 05:35:47 — 200.00 / 288.000 / 0.1157; 05:41:21 — 200.00 / 288.010 / 0.1148; 05:43:54 — 200.00 / 288.010 / 0.1158.
- **241203510005:** 05:28:07 — 262.96 / 1001.590 / 0.1663; 05:30:40 — 262.96 / 1001.600 / 0.1647; 05:35:41 — 262.96 / 1001.610 / 0.1627; 05:41:15 — 262.96 / 1001.630 / 0.1418; 05:43:48 — 262.96 / 1001.640 / 0.1439.
- **241203510003:** 02:10:56 — 370.16 / 1912.110 / 0.2238; 02:15:57 — 370.16 / 1912.130 / 0.2243; 02:24:42 — 370.16 / 1912.170 / 0.2241; 02:28:52 — 370.16 / 1912.180 / 0.2240; 02:33:53 — 370.16 / 1912.200 / 0.2249.
- **241203510004:** 05:28:07 — 1261.20 / 623.260 / 0.0052; 05:30:40 — 1261.20 / 623.260 / 0.0052; 05:35:41 — 1261.20 / 623.260 / 0.0053; 05:41:15 — 1261.20 / 623.260 / 0.0058; 05:43:48 — 1261.20 / 623.260 / 0.0052.
- **241203510002:** 05:28:10 — 1462.88 / 401.190 / 0.1190; 05:30:43 — 1462.88 / 401.200 / 0.1171; 05:35:44 — 1462.88 / 401.210 / 0.1144; 05:41:18 — 1462.88 / 401.220 / 0.1162; 05:43:51 — 1462.88 / 401.220 / 0.1172.
- **241203510010:** 05:29:35 — 1765.28 / 2073.520 / 0.3109; 05:32:08 — 1765.28 / 2073.540 / 0.2994; 05:37:42 — 1765.28 / 2073.570 / 0.2988; 05:42:43 — 1765.28 / 2073.590 / 0.3028; 05:45:16 — 1765.28 / 2073.600 / 0.3073.
- **241203510009:** 05:25:52 — 4083.04 / 1513.090 / 0.0044; 05:30:26 — 4083.04 / 1513.090 / 0.0043; 05:35:27 — 4083.04 / 1513.090 / 0.0043; 05:38:00 — 4083.04 / 1513.090 / 0.0043; 05:43:34 — 4083.04 / 1513.090 / 0.0043.

## Phase 2 — refund implementation audit and local fix

### Before the fix

- Money frames were durably reserved with a unique transaction ID/idempotency key and `max_attempts=1`.
- Refund used `DI=070108FF`; the listener matched money acknowledgements only on `C=0x83` and the expected DI.
- Ambiguous socket outcomes were made uncertain and were not retried.
- Recharge reconciled against `before_balance + amount`.
- Refund never became verified: even a matching post-ACK physical balance was forced to `uncertain`.
- No immediate `028011FF` query followed a money ACK; reconciliation depended on a later normal reading.
- Only consumed order `1240826202124140` was hard-blocked in code; `1240826202124141` was not.

### Minimal code changes

- `smart_meter/services/prepaid_money.py`
  - Blocks both consumed orders `1240826202124140` and `1240826202124141`.
  - Refund expected balance is `before_balance - refund_amount`.
  - Uses a maximum reconciliation tolerance of Rs 0.005; because authoritative values are stored to Rs 0.01, this effectively requires the same cent value.
  - Marks command and prepaid transaction `verified` only when the authoritative physical balance matches expected.
  - Mismatch or missing post-ACK verification remains uncertain and explicitly says not to retry.
- `smart_meter/management/commands/meter_listener.py`
  - After the matching money ACK, sends one `028011FF` read immediately.
  - Validates meter number, DI, and parsed balance before reconciliation.
  - A query send ambiguity, timeout, malformed response, or mismatch does not enqueue a second money command.
- `smart_meter/test_prepaid_money_lifecycle.py`
  - Adds regression coverage for full-wallet refund to zero, mismatch uncertainty, both consumed orders, and automatic post-ACK read/reconciliation using a valid captured `028011FF` frame.

Timestamped backups:

` .codex_file_backups/legacy_prepaid_20260826_024855/ `

### Test evidence

- `python manage.py test smart_meter.test_prepaid_money_lifecycle --keepdb`: **18 passed**.
- `python manage.py test smart_meter --keepdb`: **162 passed**.
- `python manage.py check`: **passed; no issues**.
- `git diff --check`: **passed**.

## Phase 3 — dry-run clearing plan (no sends)

The proposed orders below were validated as unique, absent from persisted prepaid transactions and command idempotency keys, outside the consumed-order set, and round-tripped through the manufacturer refund frame decoder with `DI=070108FF`. They are proposals only; uniqueness and the physical balance must be revalidated immediately before use. All meters currently lack a `MeterPrepaidPilot` record, so the required lifecycle audit container must be created just-in-time without altering any balance.

| Meter | Property / unit | Inventory balance | Proposed refund | Proposed order | Reading age at plan | Status | Eligibility / reason |
|---|---|---:|---:|---|---:|---|---|
| 241203510006 | F56 Basement / Room 06 | 18.96 | fresh full wallet | 18CF34D8473743CB | 18 sec | Online by persisted threshold | ELIGIBLE; selected pilot; require fresh read |
| 241203510007 | F56 Basement / Room 02 | 57.36 | fresh full wallet | 18CF34D84911067C | 12 sec | Online by persisted threshold | ELIGIBLE; require fresh read |
| 241203510001 | F56 Basement / Room 01 | 122.00 | fresh full wallet | 18CF34D84A517012 | 8 sec | Online by persisted threshold | ELIGIBLE; require fresh read |
| 250619510004 | F56 Basement / Room 03 | 200.00 | fresh full wallet | 18CF34D84AFD1063 | 12 sec | Online by persisted threshold | ELIGIBLE; require fresh read |
| 241203510005 | F56 Basement / Room 10 | 262.96 | fresh full wallet | 18CF34D84B65A893 | 18 sec | Online by persisted threshold | ELIGIBLE; require fresh read |
| 241203510003 | F54 / Flat 01 | 370.16 | none until fresh | 18CF34D84C403AB8 (do not use while stale) | 3h 21m | Offline/stale | SKIP: no fresh balance |
| 241203510004 | F56 Basement / Room 05 | 1261.20 | fresh full wallet | 18CF34D84D055B48 | 18 sec | Online by persisted threshold | ELIGIBLE; require fresh read |
| 241203510002 | F56 Basement / Room 04 | 1462.88 | fresh full wallet | 18CF34D84E2FA0D2 | 15 sec | Online by persisted threshold | ELIGIBLE; require fresh read |
| 241203510010 | F54 / Flat 02 | 1765.28 | fresh full wallet | 18CF34D84F4D2484 | 3m 50s | Online by persisted threshold | ELIGIBLE; require fresh read |
| 241203510009 | F54 / Flat 03 | 4083.04 | fresh full wallet | 18CF34D8500B7BDB | 3m 32s | Online by persisted threshold | ELIGIBLE; require fresh read |

At planning time there were no `MeterPrepaidRecharge` records and no pending/uncertain prepaid transactions. Two older standalone money commands exist for meter `260305510012`; neither is attached to any positive-balance target. The proven refund command is acknowledged with `attempt_count=1` and `max_attempts=1`.

## Pilot and bulk status

Pilot target: **241203510006**, the smallest fresh positive balance (inventory Rs 18.96).

Pilot result: **NOT STARTED — no money command sent**.

Reason: the production listener process started before the Phase 2 code fix. Reloading it is mandatory so the post-ACK balance read and verified refund reconciliation are active. The current shell received:

- `systemctl restart tms-meter-listener.service` → interactive authentication required.
- `sudo -n systemctl restart tms-meter-listener.service` → password required.

Do not queue the pilot under the old process. After an authorized restart, resume with:

1. Confirm the service is active with a new PID/start timestamp.
2. Confirm no pending/claimed/waiting/retry money command exists.
3. Obtain a fresh physical `028011FF` from `241203510006`.
4. Re-check balance, energy, timestamp, connection, unresolved transaction state, meter number, amount encoding, and order uniqueness.
5. Create the audit container and issue exactly one full-wallet refund with `max_attempts=1`.
6. Require `TX_TO_METER`, matching `C=0x83 / DI=070108FF`, a new `028011FF` balance of `0.00`, and both records `verified`.
7. If any evidence is ambiguous, stop without retry and do not start bulk.

## Current summary

- Total meters checked: 45 live meters (36 active meters; 45 total meter records).
- Positive legacy balances: 10.
- Appeared deducting: 0.
- Appeared not deducting: 9 (including one now-stale meter whose historical window was conclusive).
- Insufficient consumption data: 1.
- Physically zeroed by this run: 0.
- Uncertain created by this run: 0.
- Offline/stale positive meters: 1.
- Skipped for safety pending restart: all 10; no refund attempted.

## Final inventory

Not yet performed because the workflow stopped before the pilot. The required final groups (VERIFIED ZERO, STILL HAS BALANCE, UNCERTAIN — DO NOT RETRY, OFFLINE / COULD NOT VERIFY, and SKIPPED FOR SAFETY) must be appended after the authorized service restart, pilot, and any permitted sequential processing.

## Post-restart continuation — superseding status

The operator restarted `tms-meter-listener.service`. Verification showed:

- Active PID: `1152645`.
- Start time: `2026-08-26 08:03:48 CEST`, after the tested safety-code timestamps.
- The listener resumed valid production `028011FF` ingestion.

### Fresh-read pilot selection

The original smallest-balance candidate, `241203510006` (Rs 18.96), was skipped before any financial command. Explicit read command `346` sent only `028011FF`, exhausted five ordinary read attempts, and ended `failed` with `timeout waiting for reply`. No prepaid audit row, transaction, or money command was created for that meter.

The next-smallest candidate, `241203510007`, completed explicit read command `347` on its third ordinary read attempt. Its authoritative reply was:

- Meter: `241203510007`.
- Control: `0x91`.
- DI: `028011FF`.
- Timestamp: `2026-08-26 06:10:49.766656 UTC`.
- Balance: Rs 57.36.
- Total energy: 361.020 kWh.
- Total power: 0.000 kW.

There was no existing pending/uncertain prepaid transaction, no open money command, and the selected order was absent from both transaction IDs and command idempotency keys. The refund frame round-tripped through the manufacturer decoder as `DI=070108FF`, amount Rs 57.36, with the `incl_1st68` checksum.

### Pilot result — UNCERTAIN, DO NOT RETRY

Exactly one refund was reserved:

| Field | Evidence |
|---|---|
| Meter | `241203510007` |
| Property / unit | F56 Basement / F56-ROOM# 02 |
| Before balance | Rs 57.36 |
| Refund amount | Rs 57.36 |
| Order | `18CF46216D773CB4` |
| MeterCommand | `348` |
| Prepaid transaction | `1` |
| Expected DI | `070108FF` |
| Attempt count / maximum | 1 / 1 |
| Command status | `sent` (uncertain transport outcome) |
| Transaction status | `uncertain` |
| Application ACK | None |
| Reconciliation | Not verified; no ACK |
| Error | `Prepaid money transmission outcome is uncertain. Do not retry. Verify meter balance. Detail: timeout waiting for socket transmission` |

The listener later logged the exact queued refund frame as `DROP_STALE_TX` at `2026-08-26 11:12:22 +05:00`, before `sendall`. There is no `TX_TO_METER` log for that refund frame and no `C=0x83 / DI=070108FF` ACK. A subsequent valid physical `028011FF` at `2026-08-26 06:12:22.752252 UTC` reported the wallet still at Rs 57.36, energy 361.020 kWh, and power 0.000 kW.

Although this evidence indicates the queued frame expired before socket send and the wallet did not change, the durable lifecycle remains `uncertain` because synchronous transmission confirmation was ambiguous. It must not be retried automatically or manually without a separate manual-review decision. No database status was overridden.

Bulk clearing was not started because the pilot did not obtain an application ACK and did not physically verify zero.

## Final inventory after stopped pilot

### Positive-balance targets

| Meter | Property / unit | Original balance | Refund reserved | Final physical balance | Order | Command / transaction | ACK | Reconciliation | Final reading UTC | Result |
|---|---|---:|---:|---:|---|---|---|---|---|---|
| 241203510001 | F56 Basement / ROOM 01 | 122.00 | 0.00 | 122.00 | — | — | — | Not attempted | 2026-08-26 06:12:19 | STILL HAS BALANCE |
| 241203510002 | F56 Basement / ROOM 04 | 1462.88 | 0.00 | 1462.88 | — | — | — | Not attempted | 2026-08-26 06:12:10 | STILL HAS BALANCE |
| 241203510003 | F54 / FLAT 01 | 370.16 | 0.00 | 370.16 cached | — | — | — | Not attempted; stale | 2026-08-26 05:54:14 | OFFLINE / COULD NOT VERIFY |
| 241203510004 | F56 Basement / ROOM 05 | 1261.20 | 0.00 | 1261.20 | — | — | — | Not attempted | 2026-08-26 06:12:10 | STILL HAS BALANCE |
| 241203510005 | F56 Basement / ROOM 10 | 262.96 | 0.00 | 262.96 | — | — | — | Not attempted | 2026-08-26 06:12:07 | STILL HAS BALANCE |
| 241203510006 | F56 Basement / ROOM 06 | 18.96 | 0.00 | 18.96 | — | read 346 only | — | Skipped after fresh-read failure | 2026-08-26 06:12:08 | STILL HAS BALANCE / SKIPPED FOR SAFETY |
| 241203510007 | F56 Basement / ROOM 02 | 57.36 | 57.36 | 57.36 | `18CF46216D773CB4` | 348 / 1 | None | UNCERTAIN — DO NOT RETRY | 2026-08-26 06:12:22 | UNCERTAIN — DO NOT RETRY |
| 241203510009 | F54 / FLAT 03 | 4083.04 | 0.00 | 4083.04 | — | — | — | Not attempted | 2026-08-26 06:10:23 | STILL HAS BALANCE |
| 241203510010 | F54 / FLAT 02 | 1765.28 | 0.00 | 1765.28 | — | — | — | Not attempted | 2026-08-26 06:13:05 | STILL HAS BALANCE |
| 250619510004 | F56 Basement / ROOM 03 | 200.00 | 0.00 | 200.00 | — | — | — | Not attempted | 2026-08-26 06:12:13 | STILL HAS BALANCE |

### Fleet groups

1. **VERIFIED ZERO by this clearing run:** none.
2. **Currently meter-reported zero and online (not cleared by this run):** `250619510002`, `250619510008`, `250619510009`, `250619510010`, `250619510012`, `260305510002`, `260305510004`, `260305510006`, `260305510007`, `260305510009`, `260305510010`, `260305510011`, `260305510012`, `260305510013`, `260305510014`, `260305510015`, `260305510019`, `260305510020`, `260305510021`.
3. **STILL HAS BALANCE and online:** `241203510001`, `241203510002`, `241203510004`, `241203510005`, `241203510006`, `241203510009`, `241203510010`, `250619510004`.
4. **UNCERTAIN — DO NOT RETRY:** `241203510007`, order `18CF46216D773CB4`, command `348`, transaction `1`.
5. **OFFLINE / COULD NOT VERIFY:** `241203510003` (positive Rs 370.16), `241203510008`, `250619510001`, `250619510003`, `250619510005`, `250619510006`, `250619510007`, `250619510011`, `250619510015`, `250619510016`, `250619510018`, `250619510020`, `260305510003`, `260305510005`, `260305510016`, `260305510017`, `260305510018`.
6. **SKIPPED FOR SAFETY:** every positive-balance meter other than the one uncertain pilot; no bulk refund was attempted. Meter `241203510006` was specifically skipped after explicit fresh-read failure.

Final counts:

- Total live-meter records inventoried: 45.
- Positive legacy balances: 10.
- Appeared deducting: 0.
- Appeared not deducting: 9.
- Insufficient deduction data: 1.
- Successfully physically zeroed by this run: 0.
- Uncertain: 1.
- Offline/stale fleet records: 17, including one positive-balance target.
- Financial commands reserved: 1.
- Financial send attempts: 1, maximum 1.
- Application ACKs: 0.
- Bulk financial commands: 0.

The report was backed up before this append at `.codex_file_backups/legacy_prepaid_20260826_081200/LEGACY_PREPAID_BALANCE_CLEARING_2026-08-26.md`.

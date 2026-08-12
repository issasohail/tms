# DL/T645 prepaid protocol gap recorded for the 2026-08-11 snapshot

The implementation request references `/mnt/data/meter smart-20260804T112222Z-1-001.zip`, but that archive is not present in the supplied `TMS_2026-08-11_04-30-38_0d47887a.zip` or as a separate uploaded file in this task.

Repository code contains useful protocol evidence, including existing relay frame construction and live parsing of DI `028011FF`, but that is insufficient to safely implement native recharge or authoritative relay-state verification.

The following manufacturer-defined information is required before enabling those operations:

1. Exact relay status query DI/control code and response bit/value mapping for ON/OFF.
2. Exact prepaid parameter DIs, encodings, scaling, byte order and write authentication/password requirements for each target parameter.
3. Exact recharge/electricity-purchase DI/control code and payload format.
4. Manufacturer purchase sequence/count behavior and duplicate/replay rules.
5. Recharge acknowledgement/error response semantics and how to determine an uncertain transaction.
6. Balance-before/balance-after DI and scaling suitable for read-back reconciliation.
7. Model and firmware compatibility matrix.

Until those values are supplied and verified against the physical pilot meter, the code intentionally leaves native prepaid writes/recharge disabled and does not guess reserved fields.

## Manufacturer documents supplied on 2026-08-11

The later manufacturer upload closes one important gap for the 2024 Wi-Fi meter:
DI `0x028011FF` includes a two-byte running status word. The manufacturer table defines
Bit8 as the switch/relay state (`0` = closed/energised, `1` = tripped/open) and Bit15
as the power-protection flag. TMS v3 therefore treats Bit8 as the authoritative relay
state for this meter family instead of the former current/power heuristic.

The DL/T645 appendix also documents standard read identifiers for prepaid balances:

- `00900100` — current remaining prepaid energy (kWh)
- `00900101` — current overdraft energy (kWh)
- `00900200` — current remaining monetary amount
- `00900201` — current overdraft monetary amount

These identifiers are recorded as protocol evidence, but v3 does not alter the stable
production reading loop to poll them automatically. The existing `028011FF` snapshot
continues to be the safe read-only pilot source until explicit per-DI polling is tested
against the allowlisted physical meter.

The Unified Prepayment System API document remains excluded as an implementation path
because it is a token/encryption API. It may be used only as background evidence. An
exact native DL/T645 recharge/purchase command, authentication method, sequence/replay
rules, and read-back semantics are still not sufficiently established for safe recharge.
Recharge therefore remains disabled; no DI is guessed.

# Prepaid DL/T645 offline audit and frame report

Date: 2026-08-21
Scope: local source audit, offline byte generation, and automated tests only. No
meter socket was opened, no `meter_send` transport was executed, and no physical
`MeterCommand` was created or dispatched.

## 1. Files changed

- `smart_meter/dlt645.py`
- `smart_meter/dlt645_money.py`
- `smart_meter/management/commands/meter_listener.py`
- `smart_meter/management/commands/meter_send.py`
- `smart_meter/test_dlt645_prepaid.py` (new)
- `smart_meter/test_meter_listener_connections.py`
- `smart_meter/views.py`
- `docs/prepaid_dlt645_offline_audit_2026-08-21.md` (new)

Timestamped backups of every changed pre-existing file are outside the repository at:

`C:\Users\Sohail\.codex\backups\tenant_management_system\20260821-042946`

## 2. Active runtime paths

Before this change:

- `meter_send` imported the active (last-defined) `build_topup_frame`,
  `build_init_amount_frame`, and `build_refund_frame` from `smart_meter.dlt645` and
  could transmit them over a control or raw socket.
- The display-balance views imported `build_amount_init_frame` from
  `smart_meter.dlt645_money` and passed its result to `_call_send`.
- The routed `/control/prepaid/` view is `views_prepaid.prepaid_params`; it and the
  callable `send_prepaid_frame` management command use
  `smart_meter.vendor.prepaid.DLT645_2007_Prepaid` for the large `070104FF`
  parameter-setting structure. `views.py` contains another vendor-builder view that is
  not the routed URL. This family is not the recharge/init/refund family.

After this change:

- The canonical money-frame assembly path is `smart_meter.dlt645`.
- `smart_meter.dlt645_money` public functions are FE-prefixed compatibility wrappers
  around the canonical functions and require all security fields explicitly.
- `meter_send` refuses `topup`, `init`, and `refund` before opening a socket.
- The two display-balance views report that writes are disabled and do not call the
  sender.
- No automatic recharge/init/refund path is enabled.
- Existing vendor `070104FF` parameter builders were not consolidated or changed.

## 3. Duplicate and dead implementations

- `smart_meter.dlt645` previously defined `build_frame` three times, and defined each
  recharge/init/refund builder twice. Python used only the last definitions. The dead
  duplicate blocks were removed and one canonical set remains.
- `smart_meter.dlt645_money` remains only as a compatibility module. Its executable
  zero-MAC legacy implementations were removed; both public names delegate to the
  canonical path and require explicit security fields.
- `smart_meter.prepaid` and `smart_meter/vendor/prepaid-v1.py` are inactive variants
  for parameter setting. Runtime imports resolve to `smart_meter/vendor/prepaid.py`.
- The source tree labels files under `smart_meter/vendor/` as vendor variants, but no
  signed manufacturer provenance or authoritative document is present. Authorship
  cannot be established from filenames/comments alone.

## 4. Canonical builder choice

`smart_meter.dlt645` was chosen because it already owns the live reply checksum
verification/parser and the only runtime imports for the three money DIs came from
that module. The refactor is limited to outbound builder code; incoming parsing,
`028011FF` storage, and relay builders are unchanged.

Audited outbound structure summary:

| Implementation | Plain DI bytes | Control | Main following fields |
| --- | --- | ---: | --- |
| canonical top-up | `FF 02 01 07` | `03` | operator, amount, 8-byte order, MAC1, 6-byte schedule, mailing address, MAC2 |
| canonical init | `FF 03 01 07` | `03` | operator, amount, MAC1, 4-byte purchase count, MAC2 |
| canonical refund | `FF 08 01 07` | `03` | operator, amount, 8-byte order, MAC1, 6-byte schedule, mailing address, MAC2 |
| active vendor parameter builder | `FF 04 01 07` | `14` | switch times/counts, ratios, alarms/limits, load settings, tariff/step values and prices |
| inactive top-level/vendor-v1 variants | `07 01 04 FF` | `14` | older parameter structure; DI byte order differs from the active vendor builder |

All DATA bytes are transformed by `+0x33`. No code or documentation establishes a
refund-only security rule beyond the differing DI; the existing refund field layout
was preserved as an offline structure, not asserted as manufacturer-correct.

## 5. Checksum behavior before the change

- Active `smart_meter.dlt645` money and read builders: `std` (`C` through DATA).
- `smart_meter.dlt645_money`: checksum began at `inner[4:]`, partway through the
  address. It matched none of `std`, `incl_2nd68`, or `incl_1st68`.
- Active `smart_meter.vendor.prepaid`: `incl_2nd68` (`frame[7:]`).
- Inactive top-level `smart_meter.prepaid` and `vendor/prepaid-v1.py`: sum excluding
  only the first `68` (`frame[1:]`), another unmatched window.
- Other read/switch helpers differ and were not globally modified.

## 6. Checksum behavior after the change

`build_frame` accepts exactly:

- `std`
- `incl_2nd68`
- `incl_1st68`

Its generic default remains `std` to preserve existing generic read behavior. Every
money builder requires the caller to pass `checksum_mode` explicitly. No universal
manufacturer default was introduced. Incoming `verify_checksum` behavior is unchanged.

## 7. Known production capture validation

Captured transmitted query:

`681200510503266803083235B43A445566773716`

Calculated checksum values:

- `std`: `D6`
- `incl_2nd68`: `3E`
- `incl_1st68`: `37` (matches captured checksum)

Result: `(True, "incl_1st68")`.

Captured response:

`6812005105032668C301355A16`

It also validates as `incl_1st68`, has control `C3`, and its encoded data byte `35`
decodes to `02` after subtracting `33`.

These results validate captured byte structure/checksums only. They do not establish
acceptance of any money-changing command.

## 8. Amount encoding results

The refactor preserves the project's existing two-decimal, zero-padded BCD digit-pair
representation. The manufacturer byte order remains unconfirmed.

| Rupees | Plain bytes | After +33 |
| ---: | --- | --- |
| 0.00 | `00 00 00 00` | `33 33 33 33` |
| 1.00 | `00 00 01 00` | `33 33 34 33` |
| 50.00 | `00 00 50 00` | `33 33 83 33` |
| 100.00 | `00 01 00 00` | `33 34 33 33` |

This is digit-pair order as currently implemented, not confirmed DL/T645 monetary
endianness. No alternative order was guessed.

## 9. Operator-code findings

- `smart_meter.dlt645` previously defaulted to plain bytes `11 22 33 44`.
- `smart_meter.dlt645_money` used `44 33 22 11` and described that as little-endian.
- No production evidence establishes which order applies to these three money DIs.
- Canonical functions now require explicit four-byte `operator`; neither ordering is
  selected silently.

## 10. FE preamble findings

- `smart_meter.dlt645` previously returned frames starting at `68`.
- `smart_meter.dlt645_money` and vendor parameter builders include `FE FE FE FE`.
- `meter_send` already had a separate `--wakeup` option, so adding FE inside canonical
  frames by default could duplicate the preamble.
- Canonical `build_frame` therefore defaults to no preamble and has an explicit
  `include_preamble` option. The compatibility wrappers request FE once.

## 11. MAC1/MAC2 findings

Previous money builders silently used `00 00 00 00` for MAC1/MAC2. Canonical public
builders now require explicit four-byte values and validate their lengths. The offline
tests use conspicuously fake non-zero values; those values are not production material.

## 12. ESAM/authentication findings

No ESAM authentication, key management, key diversification, cipher generation, MAC
generation, customer-number derivation, secure random generation, or replay protection
implementation was found. None was invented. Transport entry points remain disabled.

## 13. Remaining unknowns

- Verified operator-code byte order.
- Whether amount digit pairs require a different byte order or scale.
- Exact MAC1/MAC2 algorithms and inputs.
- ESAM keys, authentication handshake, ciphertext, random/challenge semantics, and
  diversification factors.
- Customer/mailing address definition and length.
- Schedule-number semantics.
- Purchase/order number encoding, monotonicity, duplicate/replay behavior, and refund
  relationship.
- Purchase-count source and update rules.
- Exact success/error reply controls and read-back requirements for `070102FF`,
  `070103FF`, and `070108FF`.
- Whether captured `incl_1st68` evidence applies to the money-write DIs and firmware.

## 14. Offline frame structures

All fields below are explicitly fake test fixtures:

- operator: `11223344`
- MAC1: `01020304`
- MAC2: `A1A2A3A4`
- order: `0102030405060708`
- schedule: `101112131415`
- mailing address: `202122232425`
- purchase count: `00000001`
- checksum mode: `incl_1st68` for fixture coverage based on captured checksum evidence

Generated frames (no FE prefix):

- Top-up, fake Rs 50.00:
  `681200510503266803283235343A44556677333383333435363738393A3B34353637434445464748535455565758D4D5D6D77D16`
- Init amount, fake Rs 100.00:
  `681200510503266803183236343A44556677333433333435363733333334D4D5D6D78E16`
- Refund, fake Rs 1.00:
  `68120051050326680328323B343A44556677333334333435363738393A3B34353637434445464748535455565758D4D5D6D73416`

Each validates locally as `incl_1st68`. They are structural fixtures only and must not
be transmitted.

## 15. Exact commands and results

Commands run:

```text
python -m py_compile smart_meter\dlt645.py smart_meter\dlt645_money.py smart_meter\management\commands\meter_send.py smart_meter\management\commands\meter_listener.py smart_meter\test_meter_listener_connections.py smart_meter\test_dlt645_prepaid.py smart_meter\views.py
python manage.py test smart_meter.test_dlt645_prepaid smart_meter.test_meter_listener_connections --keepdb
python manage.py check
python manage.py test smart_meter --keepdb
git diff --check -- smart_meter/dlt645.py smart_meter/dlt645_money.py smart_meter/management/commands/meter_listener.py smart_meter/management/commands/meter_send.py smart_meter/test_meter_listener_connections.py smart_meter/test_dlt645_prepaid.py smart_meter/views.py
```

Results:

- Python compilation: passed for every modified Python file.
- Targeted tests: 24 passed.
- Django check: passed, no issues.
- Full `smart_meter` tests: 96 passed.
- Diff whitespace check: passed.
- A non-test-affecting Windows GLib warning about the Outlook UWP registration appeared
  during test startup.

## 16. Git diff summary

Pre-existing unrelated working-tree changes were not modified. The prepaid work changes
six tracked Python files, adds one Python test module, updates one existing listener test
module, and adds this report. No migration, settings, environment, deployment, nginx,
systemd, or relay-frame change was made. Nothing was committed or pushed.

## 17. Recommendation for the next physical test (do not send yet)

The safest single next candidate is the already captured, read-only security-status
query—not any recharge/init/refund frame:

`681200510503266803083235B43A445566773716`

Reason: production has already shown that meter `260305510012` responds to this exact
read-only frame, its `incl_1st68` checksum is reproducible, and the captured response
decodes to status byte `02`. A controlled production operator should send it at most
once, capture the complete raw response, timestamp/firmware context, and compare it to
`6812005105032668C301355A16`. Stop after that read-only observation. Do not proceed to
`070102FF`, `070103FF`, or `070108FF` until the remaining security and transaction
unknowns are resolved from authoritative manufacturer material.

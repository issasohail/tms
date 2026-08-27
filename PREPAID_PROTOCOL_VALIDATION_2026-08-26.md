# Prepaid Protocol Validation — 2026-08-26

## Scope and safety outcome

This audit used only the dedicated physical test meter `260305510012`. No relay command, recharge, refund, database balance adjustment, reset, cumulative-energy reset, or customer-meter tariff write was performed during this validation. The only physical parameter frame that reached a meter was the one controlled Phase 4 write documented below.

The Phase 4 write produced a checksum-valid negative `C=0xC3`, error byte `0x01` response, but immediate `070104FF` readback nevertheless showed a partial parameter change including byte offset 89 outside the intended tariff area. All later write, money, relay, deduction, ladder, and set-switch experiments were therefore stopped. **Classification: PROVEN_BY_PHYSICAL_TEST.**

The meter must remain quarantined from further prepaid writes until the manufacturer explains the error response, the apparent field displacement, and a safe recovery procedure. **Classification: STRONG_INFERENCE.**

## 1. Vendor-source audit

### Source status

| File | Finding | Classification |
|---|---|---|
| `smart_meter/vendor/build_prepaid_parameters.py` | Most complete manufacturer-derived builder. Produces the 143-byte parameter payload and wraps it with `C=0x03`, operator `77 66 55 44`, and checksum beginning at the first `68` after the preamble. | PROVEN_BY_VENDOR_CODE |
| `smart_meter/vendor/topup.py` | Manufacturer-derived common recharge/refund builder for `070102FF` and `070108FF`. | PROVEN_BY_VENDOR_CODE |
| `smart_meter/vendor/switch_OnOff.py` | Relay frame builder using outer `C=0x1C`, inner commands `0x1A`/`0x1B`, and a fixed validity block. | PROVEN_BY_VENDOR_CODE |
| `smart_meter/vendor/prepaid.py` | Older/inconsistent builder: `C=0x14`, no operator field, different checksum window, and DI representation that differs from the new manufacturer builder. Unsafe as the canonical writer. | PROVEN_BY_VENDOR_CODE |
| `smart_meter/vendor/prepaid_vendor.py` | Byte-identical to `smart_meter/prepaid.py`; still uses `C=0x14`, omits the operator, and has inconsistent DI/checksum assumptions. | PROVEN_BY_VENDOR_CODE |
| `smart_meter/vendor/prepaid-v1.py` | Older `C=0x14` implementation; integer-only price handling and no operator field. | PROVEN_BY_VENDOR_CODE |
| `smart_meter/vendor/prepaid command.jpeg` | Screenshot repeats the mock tariff/step values already present in `build_prepaid_parameters.py`; it adds no protocol semantics. | PROVEN_BY_VENDOR_CODE |
| `smart_meter/vendor/topup_command.jpeg` | Screenshot repeats the example recharge/refund calls in `topup.py`; it adds no protocol semantics. | PROVEN_BY_VENDOR_CODE |

### Protocol map

| Item | Function/file | DI / control | Security/operator | Payload and encoding | Checksum / response | Classification |
|---|---|---|---|---|---|---|
| A. Recharge | `make_charge`, `vendor/topup.py` | `070102FF`, `C=0x03` | raw `77 66 55 44`; two four-byte `33` MAC placeholders in vendor frame | `L=0x22`: DI 4, operator 4, amount 4-byte little-endian binary cents +33, order 8-byte reversed +33, MAC1 4, reversed meter address +33 6, MAC2 4 | sum from first `68`; physical success `C=0x83` with matching DI | PROVEN_BY_BOTH |
| B. Refund | `make_charge`, `vendor/topup.py` | `070108FF`, `C=0x03` | same as recharge | same 34-byte layout | sum from first `68`; physical success `C=0x83` with matching DI | PROVEN_BY_BOTH |
| C. Prepaid parameter write | `make070104ff` + `make_general03_cmd`, `vendor/build_prepaid_parameters.py` | `070104FF`, `C=0x03` | raw operator `77 66 55 44`; no password in source; optional seqno omitted | 143-byte +33 payload; `L=0x97`; complete frame 167 bytes | sum from first `68`; source has no response parser | PROVEN_BY_VENDOR_CODE |
| D. Prepaid parameter read | TMS `build_frame` with read DI | `070104FF`, `C=0x11` | none | DI reversed +33, `L=4` | meter requires first-`68` checksum and returns `C=0x91`, `L=0x93`, DI + 143 bytes | PROVEN_BY_PHYSICAL_TEST |
| E. Relay ON/OFF | `frame_command`, `vendor/switch_OnOff.py` | no DI; outer `C=0x1C`; inner OFF `0x1A`, ON `0x1B` | fixed eight bytes `35 33 33 33 33 33 33 33`; channel byte `34`; six-byte validity | `L=0x10` | sum beginning at first `68`; vendor file has no response parser | PROVEN_BY_VENDOR_CODE |
| F. Tariff configuration | `make070104ff` | within `070104FF` | inherited from parameter write | two sets × four prices, little-endian packed BCD, value ×10000, then +33 | write result is firmware-specific | PROVEN_BY_VENDOR_CODE |
| G. Ladder/step pricing | `make070104ff` | within `070104FF` | inherited | two threshold sets ×3 and two step-price sets ×4; ×10000 packed BCD then +33 | band semantics absent | PROVEN_BY_VENDOR_CODE |
| H. Time-zone/time-period configuration | `make070104ff` only contains two change dates and counts | within `070104FF` | inherited | dates and counts only | no function programs actual zones, sections, weekdays, holidays, or schedules | UNKNOWN |
| I. Warning balance | `warnlowbala1/2` in `make070104ff` | within `070104FF` | inherited | four-byte little-endian packed BCD, currency ×100, then +33 | semantics beyond names absent | PROVEN_BY_VENDOR_CODE |
| J. Overdraft | `creditVal` | within `070104FF` | inherited | four-byte little-endian packed BCD, currency ×100, then +33 | exact enforcement semantics absent | PROVEN_BY_VENDOR_CODE |
| K. Maximum balance | `balancemax` | within `070104FF` | inherited | four-byte little-endian packed BCD, currency ×100, then +33 | exact enforcement semantics absent | PROVEN_BY_VENDOR_CODE |
| L. Reconnection threshold | `remainPowerOn` | within `070104FF` | inherited | four-byte little-endian packed BCD, currency ×100, then +33 | exact relay semantics absent | PROVEN_BY_VENDOR_CODE |
| M. Maximum load/power | `kwMax` | within `070104FF` | inherited | three-byte little-endian packed BCD, kW ×10000, then +33 | exact trip behavior absent | PROVEN_BY_VENDOR_CODE |
| N. Load trip delay | `sleepKw` | within `070104FF` | inherited | one raw byte, then +33 with the rest of payload | name/comment says seconds; enforcement absent | PROVEN_BY_VENDOR_CODE |
| O. Switch dates | four `*ChgDate` fields | within `070104FF` | inherited | five bytes, source formats 10 decimal digits then reverses bytes and +33 | mock values use `YYYYMMDD` padded to 10 digits; firmware date/time interpretation not proven | STRONG_INFERENCE |
| P. Rate/step counts | `qtyprice`, `qtystep` | within `070104FF` | inherited | one packed-BCD byte each, capped at 4 and 3 | operational effect absent | PROVEN_BY_VENDOR_CODE |
| Q. Settlement/billing-cycle fields | none | none | none | absent | absent | UNKNOWN |
| R. Active-rate/current-tier reads | none in vendor files | none | none | absent | physical probe of `0280000B` returned `C=0xD1` and no value | UNKNOWN |
| S. Error parser/table | none | observed `C3`, `D1`; no mapping | none | one error byte observed in some responses | manufacturer meanings absent | UNKNOWN |
| T. Reset/programming commands | none | none | none | absent | absent | UNKNOWN |

## 2. Exact 143-byte field map

Offsets are zero-based inside the 143-byte parameter payload, excluding DI and operator. Multi-byte values are created as decimal-digit BCD bytes in big-endian display order, reversed into little-endian wire order, then every payload byte receives `+0x33`. **All rows: PROVEN_BY_VENDOR_CODE.**

| Start | End | Len | Field | Meaning / scaling | Set |
|---:|---:|---:|---|---|---|
| 0 | 4 | 5 | `priceChgDate` | rate-set switch date; 10 decimal digits | — |
| 5 | 9 | 5 | `stepChgDate` | step-set switch date; 10 decimal digits | — |
| 10 | 14 | 5 | `timeAreaChgDate` | time-area set switch date; 10 decimal digits | — |
| 15 | 19 | 5 | `timeSecChgDate` | time-section set switch date; 10 decimal digits | — |
| 20 | 20 | 1 | `qtyarea` | packed BCD; capped at 2 | — |
| 21 | 21 | 1 | `qtytimertable` | packed BCD; capped at 2 | — |
| 22 | 22 | 1 | `qtytimer` | packed BCD; capped at 8 | — |
| 23 | 23 | 1 | `qtyprice` | packed BCD; capped at 4 | — |
| 24 | 24 | 1 | `qtystep` | packed BCD; capped at 3 | — |
| 25 | 27 | 3 | `pt` | transformer ratio; integer packed BCD | — |
| 28 | 30 | 3 | `ct` | transformer ratio; integer packed BCD | — |
| 31 | 34 | 4 | `warnlowbala1` | currency ×100 packed BCD | — |
| 35 | 38 | 4 | `warnlowbala2` | currency ×100 packed BCD | — |
| 39 | 42 | 4 | `creditVal` | currency ×100 packed BCD | — |
| 43 | 46 | 4 | `balancemax` | currency ×100 packed BCD | — |
| 47 | 50 | 4 | `remainPowerOn` | currency ×100 packed BCD | — |
| 51 | 53 | 3 | `kwMax` | kW ×10000 packed BCD | — |
| 54 | 54 | 1 | `sleepKw` | raw delay byte; source comment says seconds | — |
| 55 | 58 | 4 | `set1Price1` | currency/kWh ×10000 packed BCD | Set 1 |
| 59 | 62 | 4 | `set1Price2` | same | Set 1 |
| 63 | 66 | 4 | `set1Price3` | same | Set 1 |
| 67 | 70 | 4 | `set1Price4` | same | Set 1 |
| 71 | 74 | 4 | `set2Price1` | same | Set 2 |
| 75 | 78 | 4 | `set2Price2` | same | Set 2 |
| 79 | 82 | 4 | `set2Price3` | same | Set 2 |
| 83 | 86 | 4 | `set2Price4` | same | Set 2 |
| 87 | 90 | 4 | `set1Step1` | kWh ×10000 packed BCD | Set 1 |
| 91 | 94 | 4 | `set1Step2` | same | Set 1 |
| 95 | 98 | 4 | `set1Step3` | same | Set 1 |
| 99 | 102 | 4 | `set1StepPrice1` | currency/kWh ×10000 packed BCD | Set 1 |
| 103 | 106 | 4 | `set1StepPrice2` | same | Set 1 |
| 107 | 110 | 4 | `set1StepPrice3` | same | Set 1 |
| 111 | 114 | 4 | `set1StepPrice4` | same | Set 1 |
| 115 | 118 | 4 | `set2Step1` | kWh ×10000 packed BCD | Set 2 |
| 119 | 122 | 4 | `set2Step2` | same | Set 2 |
| 123 | 126 | 4 | `set2Step3` | same | Set 2 |
| 127 | 130 | 4 | `set2StepPrice1` | currency/kWh ×10000 packed BCD | Set 2 |
| 131 | 134 | 4 | `set2StepPrice2` | same | Set 2 |
| 135 | 138 | 4 | `set2StepPrice3` | same | Set 2 |
| 139 | 142 | 4 | `set2StepPrice4` | same | Set 2 |

Total: `143` bytes exactly. **Classification: PROVEN_BY_VENDOR_CODE.**

## 3. Fresh physical configuration before Phase 4

Read command record `350`, transmitted once at `2026-08-26T06:56:01.895707Z`:

```text
681200510503266811043237343A4D16
```

Raw full response:

```text
681200510503266891933237343A33383333333333333333333363333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333338333333383333333833333338333333383333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333339E16
```

Extracted 143-byte on-wire payload:

```text
3338333333333333333333336333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333833333338333333383333333833333338333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333
```

Decoded payload after subtracting `0x33`:

```text
0005000000000000000000003000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000500000005000000050000000500000005000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000
```

The response is 159 bytes without preamble, `C=0x91`, `L=0x93`, DI `070104FF`, payload 143 bytes, checksum `0x9E`; checksum validates from the first `68`. **Classification: PROVEN_BY_PHYSICAL_TEST.**

### Decoded values under the manufacturer map

| Fields | Values | Classification |
|---|---|---|
| Switch dates | `priceChgDate=500`; `stepChgDate=0`; `timeAreaChgDate=300000`; `timeSecChgDate=0` | PROVEN_BY_PHYSICAL_TEST |
| Counts | all five zero | PROVEN_BY_PHYSICAL_TEST |
| PT / CT | both zero | PROVEN_BY_PHYSICAL_TEST |
| Warning, credit, max balance, reconnect | all `0.00` | PROVEN_BY_PHYSICAL_TEST |
| `kwMax`, `sleepKw` | `0.0000`, `0` | PROVEN_BY_PHYSICAL_TEST |
| Set 1 prices | `0.0000`, `0.0000`, `0.0000`, `0.5000` | PROVEN_BY_PHYSICAL_TEST |
| Set 2 prices | four × `0.5000` | PROVEN_BY_PHYSICAL_TEST |
| All step thresholds and prices | all zero | PROVEN_BY_PHYSICAL_TEST |

The switch-date and count values are implausible as a normal configured tariff profile. Whether this resulted from older preliminary writes or represents a different firmware layout is unknown. **Classification: UNKNOWN.**

### TMS parser mismatches

`build_read_price_param_frame()` uses the generic standard checksum and produced `...EC16`; the physical meter ignored it. The first-`68` checksum produced `...4D16` and succeeded. **Classification: PROVEN_BY_PHYSICAL_TEST.**

`parse_070104ff_prices()` assumes only four fields in the final 16 payload bytes. The manufacturer map has 40 fields and places the eight simple tariff prices at offsets 55–86; the final 16 bytes are Set 2 step prices. The current parser therefore labels the wrong fields. **Classification: PROVEN_BY_VENDOR_CODE.**

The legacy `DLT645_2007_Prepaid` classes build `C=0x14` frames without the manufacturer operator field and must not be used for this meter family. **Classification: PROVEN_BY_VENDOR_CODE.**

## 4. Phase 4 flat-tariff write evidence

### Proposed and transmitted frame

`50.0000 × 10000 = 500000`, formatted as eight decimal BCD digits `00500000`, reversed to `00005000`, then `+0x33` to `33338333`. **Classification: PROVEN_BY_VENDOR_CODE.**

Before payload:

```text
3338333333333333333333336333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333833333338333333383333333833333338333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333
```

Modified payload:

```text
3338333333333333333333336333333333333333333333333333333333333333333333333333333333333333333333333333333333333333338333333383333333833333338333333383333333833333338333333383333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333
```

Intended field ranges were offsets 55–86. Actual differing byte offsets were `57,61,65,68,69,72,73,76,77,80,81,84,85`. Every byte outside offsets 55–86 was identical before transmission. **Classification: PROVEN_BY_BOTH.**

Exact frame:

```text
FEFEFEFE681200510503266803973237343A7766554433383333333333333333333363333333333333333333333333333333333333333333333333333333333333333333333333333333333333333383333333833333338333333383333333833333338333333383333333833333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333337A16
```

Validation: length `167`, `C=0x03`, `L=0x97`, DI on wire `3237343A`, operator `77665544`, checksum `0x7A`, end `16`. **Classification: PROVEN_BY_VENDOR_CODE.**

Command `352` expired in the old socket queue and was logged `DROP_STALE_TX` before `sendall`; it did not reach the meter. Command `353` was then the sole physical transmission. **Classification: PROVEN_BY_PHYSICAL_TEST.**

Log evidence:

```text
2026-08-26 12:01:33,455 TX_TO_METER ... meter=260305510012 len=167 frame=<exact frame above>
2026-08-26 12:01:33,697 RAW_UNPARSED_RX meter=260305510012 control_code=0xC3 len=13 frame=6812005105032668C301345916
```

The physical response has `C=0xC3`, `L=1`, on-wire byte `34`, decoded byte `01`, and a valid checksum. The vendor source does not define byte `01`. **Classification: PROVEN_BY_PHYSICAL_TEST** for the bytes; **Classification: UNKNOWN** for their manufacturer-specific meaning.

### Immediate readback and unexpected partial application

Readback command `354` returned:

```text
681200510503266891933237343A3338333333333333333333336333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333338333333383333333833333338333333383333333833333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333EE16
```

Before/after payload differences: offsets `68,69,72,73,76,77,80,81,84,85,89`. Offset 89 is outside the intended 55–86 tariff range. **Classification: PROVEN_BY_PHYSICAL_TEST.**

| Manufacturer-map field | Before | After | Classification |
|---|---:|---:|---|
| `set1Price1` | 0.0000 | 0.0000 | PROVEN_BY_PHYSICAL_TEST |
| `set1Price2` | 0.0000 | 0.0000 | PROVEN_BY_PHYSICAL_TEST |
| `set1Price3` | 0.0000 | 0.0000 | PROVEN_BY_PHYSICAL_TEST |
| `set1Price4` | 0.5000 | 50.0000 | PROVEN_BY_PHYSICAL_TEST |
| `set2Price1..4` | each 0.5000 | each 50.0000 | PROVEN_BY_PHYSICAL_TEST |
| `set1Step1` | 0.0000 | 50.0000 | PROVEN_BY_PHYSICAL_TEST |

The meter did not accept all eight requested tariff fields exactly, and it altered one mapped non-tariff field. Therefore “Rs 50 flat tariff accepted” is not proven. **Classification: UNKNOWN.**

No rollback was attempted because the negative ACK plus partial application means another write would be unsafe without manufacturer guidance. **Classification: STRONG_INFERENCE.**

## 5. Wallet deduction and effective rate

Not performed. Phase 4 did not produce a fully successful, byte-exact readback. No recharge was issued. Result: `INSUFFICIENT_DATA`. Whether the meter deducts at Rs 50/kWh is **Classification: UNKNOWN.**

## 6. Effect on other state

Pre-write `028011FF` command `351` was checksum-valid and parsed as balance `0.00`, energy `75.72 kWh`, voltage `244.3 V`, power `0.0239 kW`, status word `0000`. **Classification: PROVEN_BY_PHYSICAL_TEST.**

Post-write command `355` was checksum-valid but parsed as balance `5.00`, energy `0.00 kWh`, voltage/power zero, status word `0000`. Those values conflict so sharply with the preceding authoritative state that the semantics are not trusted. **Classification: PROVEN_BY_PHYSICAL_TEST** for the raw response and parser output; **Classification: UNKNOWN** for actual wallet, energy, and relay state after the write.

The write did not preserve the full `070104FF` payload. Whether it preserved wallet balance, cumulative energy, counters, and relay state cannot be safely concluded. **Classification: UNKNOWN.**

## 7. Ladder/step behavior

The source proves the presence, order, and scaling of two three-threshold sets and two four-price sets. It does not define cumulative versus incremental thresholds, final-band semantics, active-set selection, or settlement reset rules. No physical ladder test was performed after the Phase 4 safety stop. **Classification: UNKNOWN.**

## 8. Step Set 1 versus Set 2

`stepChgDate` exists, but source comments and mock data do not prove firmware resolution or active-set rules. No date-switch test was performed. **Classification: UNKNOWN.**

## 9. Rate Set 1 versus Set 2

`priceChgDate` exists and two four-price sets are encoded, but source does not prove which set is active before/after the date. No date-switch test was performed. **Classification: UNKNOWN.**

## 10. Zero-balance relay behavior

Not tested. `creditVal`, `remainPowerOn`, and two warning balances exist in the payload, but exact trip/reconnect behavior is absent from vendor code. **Classification: UNKNOWN.**

## 11. Recharge/refund side effects

The supplied known physical facts and existing logs prove recharge `070102FF` and refund `070108FF` can change the physical wallet with success `C=0x83`. The vendor builder uses the same frame structure for both except DI. **Classification: PROVEN_BY_BOTH.**

No trustworthy paired full `070104FF` before/after snapshots exist around those money operations, so effects on tariff, step state, energy, switch dates, and counts are **Classification: UNKNOWN.** No additional money operation was issued.

Consumed orders `1240826202124140` and `1240826202124141` were not reused. **Classification: PROVEN_BY_PHYSICAL_TEST.**

## 12. Error-response findings

| Evidence | Finding | Classification |
|---|---|---|
| All vendor files | no error table, bit mask, or `C3/D1/D4` parser | PROVEN_BY_VENDOR_CODE |
| Earlier `0280000B` physical read | `C=0xD1`, one-byte response; no active-tier value | PROVEN_BY_PHYSICAL_TEST |
| Phase 4 write | `C=0xC3`, decoded error byte `0x01` | PROVEN_BY_PHYSICAL_TEST |
| Meaning of `C3/01` | not present in manufacturer source | UNKNOWN |
| Meaning of prior abnormal responses | not present in manufacturer source | UNKNOWN |

No generic DL/T645 error meaning is assigned because manufacturer/firmware behavior is demonstrably nontrivial here.

## 13. Answered questionnaire

| Category / question | Answer and evidence | Classification | More testing? | Manufacturer response? |
|---|---|---|---|---|
| A. Can a flat price be encoded at four decimals? | Yes; eight price fields use ×10000 BCD. | PROVEN_BY_VENDOR_CODE | No | No |
| A. Is Rs 50 inside the wire encoding? | Yes; `33338333` on wire. | PROVEN_BY_VENDOR_CODE | No | Official range only |
| A. Did the meter accept a complete Rs 50 flat profile? | No complete acceptance: negative ACK, only five mapped prices read as 50, and a step field changed. | PROVEN_BY_PHYSICAL_TEST | Unsafe now | Yes |
| B. Are there two ladder sets with 3 thresholds and 4 prices? | Yes, exact offsets mapped. | PROVEN_BY_VENDOR_CODE | No | No |
| B. Are thresholds cumulative and is Price4 the final band? | Source does not say; physical test blocked. | UNKNOWN | Yes, after recovery | Yes |
| C. What resets the tier counter? | No settlement/cycle field or semantics found. | UNKNOWN | Long-term test unsafe | Yes |
| C. Which ladder set is active? | Not exposed by vendor source/read. | UNKNOWN | Date-switch test after recovery | Yes |
| D. Are two time-area/time-section sets represented? | Change dates and counts are represented. | PROVEN_BY_VENDOR_CODE | No | No |
| D. How are weekdays, holidays, zones and daily periods programmed? | No implementation found. | UNKNOWN | Cannot test without frames | Yes |
| E. What is the manufacturer write frame? | `C=03`, DI reversed/+33, raw operator `77665544`, 143-byte +33 payload, `L=97`, first-`68` checksum, 167 bytes. | PROVEN_BY_VENDOR_CODE | No | No |
| E. Is a password required? | New manufacturer code contains none; adding one would be unsupported. | PROVEN_BY_VENDOR_CODE | No | Confirm firmware nuance only |
| F. Can the 143-byte block be read back? | Yes with `C=11`, DI `070104FF`, first-`68` checksum. | PROVEN_BY_PHYSICAL_TEST | No | No |
| F. Does current TMS parse it correctly? | No; it reads the last 16 bytes under incorrect labels and generic read checksum is rejected. | PROVEN_BY_BOTH | Unit/fixture tests needed | No |
| G. Which credit-control fields exist? | two warning balances, overdraft, max balance, reconnect threshold, max load, delay. | PROVEN_BY_VENDOR_CODE | No | No |
| G. Exact trip/reconnect semantics? | Not in source; not tested. | UNKNOWN | Yes, after recovery | Yes |
| H. Does a tariff write preserve all other parameters? | This test did not; mapped offset 89 changed unexpectedly. | PROVEN_BY_PHYSICAL_TEST | Do not repeat | Yes |
| H. Does it preserve wallet/energy/relay? | Post-read semantics became abnormal, so no reliable answer. | UNKNOWN | Diagnostic recovery first | Yes |
| I. Do recharge/refund use different DIs? | Recharge `070102FF`, refund `070108FF`. | PROVEN_BY_BOTH | No | No |
| I. Do both change physical balance? | Yes, from supplied established physical tests. | PROVEN_BY_PHYSICAL_TEST | No | No |
| I. Do they reset tariff/tier state? | No paired parameter snapshots prove this. | UNKNOWN | Possibly later | Yes |
| J. Which source should become canonical? | A new validated TMS module based on `build_prepaid_parameters.py` and `topup.py`, not any `prepaid*.py` writer. | STRONG_INFERENCE | Implement/test offline first | No |
| J. Is complete vendor source still needed? | Yes: error codes, firmware-specific layout/security header, scheduling, settlement, and recovery. | STRONG_INFERENCE | No substitute | Yes |

## 14. Remaining manufacturer questions

1. What exactly does `C=0xC3` with decoded error byte `0x01` mean for `070104FF`, and can a negative response still partially apply data?
2. For this exact firmware, what is the authoritative parameter layout/security header? Why did the documented 143-byte payload appear displaced, changing mapped offset 89?
3. What is the safe manufacturer-supported recovery/readback procedure for meter `260305510012` now that the payload and live-status response are abnormal?
4. What are the official supported minimum/maximum tariff and step values?
5. What are the complete manufacturer-specific error-code definitions for `C3`, `D1`, `D4`, and their status bytes?
6. What are the exact ladder band, active-set, settlement-cycle, and counter-reset semantics?
7. What are the complete time-zone, time-section, weekday, holiday, and daily-schedule programming/read commands?
8. What are the exact warning, zero-balance trip, overdraft, reconnect-threshold, max-load, and delay semantics?

## 15. Proposed canonical TMS implementation

1. Quarantine prepaid parameter writes behind a hard allowlist for dedicated test meters and a feature flag. **Classification: STRONG_INFERENCE.**
2. Create one canonical codec based on the exact field table, using `Decimal`, strict packed-BCD validation, explicit +33 state, explicit checksum mode, and round-trip decoding. **Classification: STRONG_INFERENCE.**
3. Correct `build_read_price_param_frame()` to select the meter-family first-`68` checksum explicitly. **Classification: PROVEN_BY_PHYSICAL_TEST.**
4. Replace `parse_070104ff_prices()` with a full 143-byte parser that rejects invalid lengths/BCD instead of coercing invalid digits. **Classification: PROVEN_BY_BOTH.**
5. Add a response classifier that records negative `C3/D1/D4` frames even when they contain no DI; the current DI waiter times out despite receiving `C3`. **Classification: PROVEN_BY_PHYSICAL_TEST.**
6. Treat `TX_TO_METER` as transport evidence only; require matching physical ACK plus full immediate readback and non-target byte comparison. **Classification: STRONG_INFERENCE.**
7. Preserve immutable before/outbound/ACK/readback snapshots in `MeterPrepaidWriteAttempt`, with `max_attempts=1` and an explicit uncertain/partial status. **Classification: STRONG_INFERENCE.**
8. Retire `vendor/prepaid.py`, `prepaid_vendor.py`, `prepaid-v1.py`, and duplicate `smart_meter/prepaid.py` only after fixtures prove no imports depend on them. **Classification: STRONG_INFERENCE.**

Required tests: exact offsets and total length; every field min/max and invalid BCD; Decimal scaling; checksum modes; known 167-byte fixture; mutation test proving only selected offsets change; negative `C3` capture; partial-readback detection; stale-socket/no-send distinction; test-meter allowlist; and full 143-byte round trip. **Classification: STRONG_INFERENCE.**

No broad refactor or production deployment was performed in this run.

## 16. Test results

| Command | Result |
|---|---|
| `python manage.py test smart_meter --keepdb` | 171 tests passed |
| `python manage.py check` | no issues |
| `git diff --check` | clean |

The tests completed before the physical write. **Classification: PROVEN_BY_PHYSICAL_TEST.**

## 17. Unresolved safety issues

- The parameter write returned a negative response but changed readback data, including one non-target mapped byte. **Classification: PROVEN_BY_PHYSICAL_TEST.**
- The post-write `028011FF` response is checksum-valid but its parsed state conflicts with the immediately preceding live state. **Classification: PROVEN_BY_PHYSICAL_TEST.**
- Actual wallet, energy, relay state, active rate, and validity of the manufacturer map for this firmware are unresolved. **Classification: UNKNOWN.**
- Do not perform another prepaid parameter write, recharge/refund, relay operation, or deduction test on this meter until the manufacturer supplies a recovery procedure and the live state is independently verified. **Classification: STRONG_INFERENCE.**


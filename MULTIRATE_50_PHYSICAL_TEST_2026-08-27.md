# Multi-rate 50 Physical Test — 2026-08-27

## Outcome and safety decision

**Final classification: STOPPED_BEFORE_WRITE / INSUFFICIENT_VENDOR_LOGIC / ABNORMAL_PREWRITE_STATE.**

No `C=0x03` prepaid parameter write was transmitted. No recharge, refund, relay command, reset, cumulative-energy reset, database correction, or deduction test was performed. The only physical transmissions made in this run were the two required read-only requests to dedicated test meter `260305510012`, each with `max_attempts=1`.

There are two independent mandatory stop conditions:

1. Manufacturer-derived code defines the 143-byte field layout and maximum count values, but does not define which `qtyprice`, Set 1/Set 2, time-table, timer, and time-area values form a valid flat multi-rate configuration. Required fields therefore remain `UNKNOWN`.
2. The fresh `028011FF` response is abnormal. Its 125-byte payload is byte-for-byte identical to the first 125 bytes of the fresh `070104FF` parameter payload. Parsing it as balance/energy/electrical state produces `5.00`, `0.00 kWh`, and zero voltage/current/power, contradicting stable recent history at `76.93 kWh`, about `240 V`, and `0.024 kW`.

The requested safety rules require stopping before the physical write when either condition occurs.

## 1. Manufacturer instruction

Manufacturer instruction supplied for this run:

> Please use the multi-rate table: 260305510012 to set the electricity price at 50 yuan and send the data frame.

Target meter: `260305510012` only.

Requested price: `50.0000` per kWh.

## 2. Interpretation of the multi-rate table

### Manufacturer-derived sources inspected

| Source | Finding |
|---|---|
| `smart_meter/vendor/build_prepaid_parameters.py` | Canonical manufacturer-derived `make070104ff` payload and `make_general03_cmd` wrapper. It defines all 143 bytes, `C=03`, `DI=070104FF`, operator `77665544`, `L=97`, empty sequence number, and checksum from the first `68`. |
| `smart_meter/vendor/prepaid command.jpeg` | Shows four tariffs in Set 1 and four tariffs in Set 2, each with distinct mock prices. It does not define flat-rate population, active-set selection, or count dependencies. |
| `smart_meter/vendor/prepaid.py` | Older incompatible `C=14` implementation; not suitable as the canonical manufacturer frame for this meter. |
| `smart_meter/vendor/prepaid_vendor.py` | Same incompatible `C=14` family as above. |
| `smart_meter/vendor/prepaid-v1.py` | Older `C=14` family; no missing multi-rate validity rules found. |

No manufacturer-derived source contains `MultPrice` or `SendRequestMultPrice` beyond the reconstructed call-site comment, nor any separate time-section/rate-table writer that explains the required relationship among the count fields.

### What the vendor code proves

| Value or rule | Classification | Evidence |
|---|---|---|
| Target price is `50.0000` | `DIRECT_FROM_VENDOR_CODE` | Direct manufacturer instruction supplied for this run. |
| Price encoding is value × 10000, eight decimal BCD digits, reversed, then `+0x33` | `DIRECT_FROM_VENDOR_CODE` | `make070104ff`; `50.0000` becomes plain `00 00 50 00`, wire `33 33 83 33`. |
| `qtyprice` occupies payload offset 23 and is capped at 4 | `DIRECT_FROM_VENDOR_CODE` | `min(..., 4)` in `make070104ff`. |
| `qtyarea`, `qtytimertable`, `qtytimer` are independently capped at 2, 2, and 8 | `DIRECT_FROM_VENDOR_CODE` | `make070104ff`. |
| Set 1 and Set 2 each contain four price slots | `DIRECT_FROM_VENDOR_CODE` | Builder and manufacturer screenshot. |
| `qtyprice` must be nonzero for a functioning price table | `STRONG_INFERENCE` | A zero count with populated prices is internally suspect, but the source contains no minimum or validation branch. |
| Flat 50 must use `qtyprice=1` and only Price1 | `UNKNOWN` | Not defined by vendor logic. |
| Flat 50 must use `qtyprice=4` and all eight slots at 50 | `UNKNOWN` | Not defined by vendor logic. |
| Set 2 must mirror Set 1 for a flat tariff | `UNKNOWN` | Active-set/date semantics are absent. |
| Valid values of `qtyarea`, `qtytimertable`, and `qtytimer` for a flat tariff | `UNKNOWN` | The code supplies only caps and mock values `2/1/6`; it does not establish required relationships. |
| Mock counts `2/1/6/4` may be copied to this meter | `UNKNOWN` | They are example configuration, not meter-specific or validated defaults. |
| Existing warning, credit, max-balance, reconnect, load, PT, and CT bytes should be preserved | `REQUIRED_BY_VENDOR_LOGIC` | A full-block writer necessarily retransmits them; the test instructions explicitly prohibit unrelated changes. |

Because `qtyprice`, timer-table counts, active rate-set selection, and Set 2 handling are required to decide what the meter will actually charge, the frame cannot be classified internally valid from the available manufacturer logic.

## 3. Fresh current meter state before any write

### `070104FF` read

Command record: `356`  
Created: `2026-08-27T01:54:03.724984Z`  
Physically transmitted: `2026-08-27T01:54:04.489113Z` (DB attempt timestamp)  
Acknowledged: `2026-08-27T01:54:07.243736Z`  
Attempt count: `1` of `1`

Read request:

```text
681200510503266811043237343A4D16
```

Complete physical response:

```text
681200510503266891933237343A3338333333333333333333336333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333338333333383333333833333338333333383333333833333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333EE16
```

Response validation: 159 bytes, `C=91`, `L=93`, matching `DI=070104FF`, 143-byte payload, checksum `EE`, valid using the manufacturer first-`68` rule.

Raw 143-byte on-wire payload:

```text
3338333333333333333333336333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333338333333383333333833333338333333383333333833333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333
```

Decoded 143-byte payload after subtracting `0x33`:

```text
0005000000000000000000003000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000005000000050000000500000005000000050000000500000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000
```

### Exact decoded configuration

| Fields | Current value |
|---|---|
| `priceChgDate`, `stepChgDate`, `timeAreaChgDate`, `timeSecChgDate` | `500`, `0`, `300000`, `0` |
| `qtyarea`, `qtytimertable`, `qtytimer`, `qtyprice`, `qtystep` | `0`, `0`, `0`, `0`, `0` |
| `PT`, `CT` | `0`, `0` |
| `warnlowbala1`, `warnlowbala2`, `creditVal`, `balancemax`, `remainPowerOn` | all `0.00` |
| `kwMax`, `sleepKw` | `0.0000`, `0` |
| `set1Price1..4` | `0.0000`, `0.0000`, `0.0000`, `50.0000` |
| `set2Price1..4` | `50.0000`, `50.0000`, `50.0000`, `50.0000` |
| `set1Step1..3` | `50.0000`, `0.0000`, `0.0000` |
| all eight step-price fields | `0.0000` |
| `set2Step1..3` | all `0.0000` |

This is the same partial/unsafe state documented after the 2026-08-26 negative-ACK write: Set 1 Price4 and all Set 2 prices are 50, but Set 1 Price1–3 remain zero and mapped non-tariff field `set1Step1` is 50.

### `028011FF` read

Command record: `357`  
Created: `2026-08-27T01:54:24.254635Z`  
Physically transmitted: `2026-08-27T01:54:24.394179Z` (DB attempt timestamp)  
Acknowledged: `2026-08-27T01:54:37.689402Z`  
Attempt count: `1` of `1`

Read request:

```text
681200510503266811043244B335D416
```

Complete physical response:

```text
681200510503266891813244B3353338333333333333333333336333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333338333333383333333833333338333333383333333833333333333333333333333333333333333333333333333333333333333333333333333CD16
```

Response validation: 141 bytes, `C=91`, `L=81`, matching `DI=028011FF`, 125-byte payload, checksum `CD`, valid using the manufacturer first-`68` rule.

Parser output:

| Item | Fresh response | Recent trustworthy history |
|---|---:|---:|
| balance | `5.00` | prior fresh/live value `0.00` |
| total energy | `0.00 kWh` | `76.93 kWh` |
| voltage A | `0.0 V` | approximately `238–243 V` |
| current A | `0.000 A` | approximately `0.281–0.289 A` |
| total power | `0.0000 kW` | approximately `0.024 kW` |
| status word | `0000` | `0000` |

The 125-byte `028011FF` payload exactly equals bytes 0–124 of the fresh 143-byte `070104FF` payload. The remaining 18 bytes of `070104FF` are all `33`. This proves that the fresh response cannot be treated as an independent health snapshot under the current field parser.

The existing listener automatically persisted the parsed `028011FF` values to `LiveReading` as part of its normal read path. No manual database reading/balance edit or correction was made. The prior historical rows remain unchanged and show the stable `76.85–76.93 kWh` progression.

## 4. Previous quantity/count issue analysis

The current block has all count fields at zero while price fields are populated. That is internally suspicious, but the manufacturer builder does not enforce any minimum or cross-field constraint. It merely clamps maximum values.

The 2026-08-26 attempt populated all eight tariff prices with 50 while preserving zero count fields. The physical response was negative `C=C3`, decoded error byte `01`, and readback partially applied the requested bytes plus changed mapped `set1Step1`. This establishes that repeating the same frame is unsafe; it does not establish which count combination would correct it.

## 5. Exact configuration selected

**No configuration was selected for physical transmission.**

Two narrow interpretations can be encoded, but vendor logic cannot determine which is valid:

1. `qtyprice=1`, Set 1 Price1=`50.0000`, Set 2 Price1=`50.0000`, preserving every other current byte.
2. `qtyprice=4`, all eight simple tariff slots=`50.0000`, preserving every other current byte.

Neither interpretation resolves whether timer/time-table counts must also be nonzero, which rate set is active, or whether the existing malformed `set1Step1=50.0000` must be repaired. Copying the mock `qtyarea=2`, `qtytimertable=1`, `qtytimer=6` would be a blind write and was rejected.

## 6. Proposed field changes and reasons

No physical change was authorized after validation.

| Candidate | Field | Old | Proposed | Reason | Classification |
|---|---|---:|---:|---|---|
| A | `qtyprice` | `0` | `1` | Enable one tariff | `STRONG_INFERENCE` |
| A | `set1Price1` | `0.0000` | `50.0000` | Put 50 in the first Set 1 slot | `STRONG_INFERENCE` |
| A | `set2Price1` | `50.0000` | unchanged | Already 50 | `STRONG_INFERENCE` |
| B | `qtyprice` | `0` | `4` | Enable four tariffs | `STRONG_INFERENCE` |
| B | `set1Price1..3` | each `0.0000` | each `50.0000` | Mirror all Set 1 slots | `STRONG_INFERENCE` |
| B | Set 1 Price4 and Set 2 Price1..4 | each `50.0000` | unchanged | Already 50 | `DIRECT_FROM_VENDOR_CODE` for encoding only; active semantics `UNKNOWN` |

## 7. Dry-built outbound frames — not approved for meter transmission

These frames are provided only to let the manufacturer identify the intended table representation. Both round-trip structurally. Neither is semantically validated or safe to send.

### Candidate A — one rate, Price1 in both sets

Payload:

```text
3338333333333333333333336333333333333333333333343333333333333333333333333333333333333333333333333333333333333333338333333333333333333333338333333383333333833333338333333383333333833333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333
```

Complete frame:

```text
FEFEFEFE681200510503266803973237343A7766554433383333333333333333333363333333333333333333333433333333333333333333333333333333333333333333333333333333333333333383333333333333333333333383333333833333338333333383333333833333338333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333332B16
```

Changed payload byte offsets: `23`, `57`.

### Candidate B — four rates, all eight simple price slots at 50

Payload:

```text
3338333333333333333333336333333333333333333333373333333333333333333333333333333333333333333333333333333333333333338333333383333333833333338333333383333333833333338333333383333333833333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333
```

Complete frame:

```text
FEFEFEFE681200510503266803973237343A776655443338333333333333333333336333333333333333333333373333333333333333333333333333333333333333333333333333333333333333338333333383333333833333338333333383333333833333338333333383333333833333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333CE16
```

Changed payload byte offsets: `23`, `57`, `61`, `65`.

## 8. Pre-write validation details

| Check | A | B | Result |
|---|---|---|---|
| Meter exactly `260305510012` | pass | pass | structural |
| Complete frame length 167 | pass | pass | structural |
| `C=03` | pass | pass | structural |
| `L=97` | pass | pass | structural |
| `DI=070104FF` | pass | pass | structural |
| Operator `77665544` | pass | pass | structural |
| Payload exactly 143 bytes | pass | pass | structural |
| First-`68` checksum valid | `2B` | `CE` | structural |
| Price bytes decode to `50.0000` in proposed slots | pass | pass | encoding only |
| Multi-rate count/table fields internally consistent per vendor code | **fail/unknown** | **fail/unknown** | mandatory stop |
| Fresh `028011FF` health state normal | **fail** | **fail** | mandatory stop |
| No unrelated unsafe current field present | **fail** (`set1Step1=50`) | **fail** (`set1Step1=50`) | mandatory stop |

## 9. Transmission evidence

Read-only TX evidence:

```text
2026-08-27 06:54:06,811 TX_TO_METER meter=260305510012 len=16 frame=681200510503266811043237343A4D16
2026-08-27 06:54:37,265 TX_TO_METER meter=260305510012 len=16 frame=681200510503266811043244B335D416
```

The worker log uses its configured local timestamp (`+05:00`); database command timestamps above are UTC.

**There is no `TX_TO_METER` entry for either 167-byte candidate. Physical write attempt count: 0.**

## 10. Physical ACK or negative ACK

Not applicable to a parameter write because no write was transmitted. Both read-only requests returned checksum-valid `C=91` responses.

## 11. Full `070104FF` readback

The complete fresh pre-write `070104FF` response and 143-byte payload are in section 3. There is no post-write readback because the test stopped before transmission.

## 12. Byte-for-byte comparison

The fresh `070104FF` state matches the unsafe post-write payload documented on 2026-08-26. No bytes were changed during this run.

Critical cross-DI comparison: fresh `028011FF` payload bytes 0–124 equal fresh `070104FF` payload bytes 0–124 exactly.

## 13. `028011FF` health check

**Failed.** The response is checksum-valid but semantically abnormal and contradicts recent energy/electrical history. It cannot establish that energy remained at `76.93+ kWh`, nor can it safely establish the physical balance.

## 14. Wallet deduction test

Not permitted. The write/readback and health prerequisites did not pass. No recharge was performed.

Result: `INSUFFICIENT_DATA`.

## 15. Effective measured rate

Not measured. Energy and balance deltas are unavailable from a trustworthy paired physical state.

## 16. Final classification

`STOPPED_BEFORE_WRITE`  
`MULTIRATE_CONFIGURATION_UNKNOWN`  
`ABNORMAL_028011FF_PREWRITE_STATE`  
`INSUFFICIENT_DATA`

Safe next step: send the two dry candidate frames and the exact questions below to the manufacturer, without transmitting either frame to the meter:

1. For a flat 50 tariff, must `qtyprice` be `1` or `4`?
2. Must both Set 1 and Set 2 be populated identically?
3. What exact `qtyarea`, `qtytimertable`, and `qtytimer` values are required?
4. Which Set is active for `priceChgDate=500`, and is that date value valid?
5. Why does a `028011FF` read return the first 125 bytes of `070104FF` under a different DI?
6. How should the unexpected `set1Step1=50.0000` be safely recovered?

## 17. Exact frame to send back to the manufacturer

There is no single frame that can honestly be labeled correct from the supplied logic. Send both Candidate A and Candidate B from section 7 to the manufacturer **for protocol clarification only**, explicitly marked `DO NOT TRANSMIT — PLEASE CONFIRM WHICH TABLE/COUNT COMBINATION IS VALID`.

## Short summary

```text
FRAME SENT: NO PARAMETER WRITE; TWO READ-ONLY FRAMES ONLY
RESPONSE: 070104FF VALID BUT UNSAFE CURRENT BLOCK; 028011FF CHECKSUM-VALID BUT ABNORMAL/PAYLOAD-ALIASED
READBACK PRICE: SET1=[0,0,0,50], SET2=[50,50,50,50]; qtyprice=0; set1Step1=50
ENERGY BEFORE: RECENT HISTORY 76.93 kWh; FRESH 028011FF UNTRUSTWORTHY (0.00)
ENERGY AFTER: NOT APPLICABLE — NO WRITE
BALANCE BEFORE: RECENT LIVE 0.00; FRESH 028011FF UNTRUSTWORTHY (5.00)
BALANCE AFTER: NOT APPLICABLE — NO WRITE
EFFECTIVE RATE: INSUFFICIENT_DATA
SAFE TO CONTINUE: NO
```

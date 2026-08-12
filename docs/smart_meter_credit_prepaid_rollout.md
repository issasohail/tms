# Smart-meter credit control and DL/T645 pilot rollout

## Safety posture

This implementation preserves the existing meter listener/parser/storage path. Credit evaluation is requested only after a valid live reading has been stored, and failures in the evaluator enqueue path are fail-open. Automatic credit evaluation, tenant notifications, cutoff, restore, prepaid reads and prepaid writes are all disabled by default.

The repository snapshot supplied for this implementation did **not** contain the manufacturer documentation ZIP named `meter smart-20260804T112222Z-1-001.zip` (nor `tms22(1).zip`). Existing repository code proves the general relay command frames and one live prepaid DI (`028011FF`), but it does not prove an authoritative relay-status DI/bit mapping or the native recharge transaction. Those protocol values are deliberately not guessed.

## Architecture

- `MeterInstallation` remains the authoritative meter/unit/lease assignment.
- `LiveReading` and `MeterReading` remain the reading source of truth.
- `MeterCommand` is extended, rather than replaced, for durable pending/offline/retry/cancel/ack/verify state.
- `MeterCreditAccount` is installation- and lease-scoped. Only one enabled account is allowed per installation.
- `MeterEvaluationRequest` is a debounced database queue. `process_meter_credit_evaluations` performs the accounting work outside the parser.
- Exposure uses existing electricity invoices/payment allocation plus unbilled cumulative kWh. The security deposit is only a reference for deriving a credit limit; it is never consumed.
- `MeterCreditAudit` records policy, financial, enforcement and command-related decisions.
- `MeterPrepaidPilot` and related read/write/recharge records isolate experimental native prepaid work from locally managed credit control.

## Billing modes

- `postpaid`: unchanged normal behavior.
- `credit_controlled`: normal postpaid invoicing plus optional local credit control.
- `prepaid`: preserved legacy value; not silently reinterpreted.
- `prepaid_pilot`: isolated DL/T645 pilot mode.

`credit_controlled` remains included in the normal postpaid invoice-generation paths. No migration bulk-enables any meter.

## Environment variables

All dangerous controls default to false:

```text
METER_ENABLE_AUTOMATIC_CREDIT_EVALUATION=False
METER_ENABLE_AUTOMATIC_NOTIFICATIONS=False
METER_ENABLE_AUTOMATIC_CUTOFF=False
METER_ENABLE_AUTOMATIC_RESTORE=False
METER_ENABLE_PREPAID_READS=False
METER_ENABLE_PREPAID_WRITES=False
METER_EMERGENCY_STOP=False
METER_CREDIT_ALLOWED_METER_IDS=
METER_PREPAID_ALLOWED_METER_IDS=
METER_AUTOMATIC_CUTOFF_PROTECTED_START=20:00
METER_AUTOMATIC_CUTOFF_PROTECTED_END=08:00
```

The two allowlists are comma-separated database meter IDs. An empty credit allowlist prevents automatic credit-control switching. An empty prepaid allowlist prevents prepaid operations.

## Permissions

Credit-account permissions include viewing/changing settings, activation/deactivation, notification mute, enforcement hold, cutoff approval, reconnect override and emergency-stop use. Command permissions include cancellation and raw DL/T645 frame viewing. Prepaid permissions include enable/read/write/recharge/rollback. The migration creates permissions only; it does not assign them to every staff user or group.

## Management commands

```bash
python manage.py process_meter_credit_evaluations --limit 100
python manage.py process_meter_credit_evaluations --meter-id 123 --dry-run
python manage.py reconcile_meter_credit_accounts --meter-id 123 --dry-run
python manage.py process_meter_commands --meter-id 123 --limit 50 --dry-run
python manage.py read_prepaid_parameters --meter-id 123
```

A guarded prepaid write entry point exists only for one explicit meter and intentionally refuses transport until manufacturer read-before/write/read-back semantics are proven:

```bash
python manage.py prepaid_meter_write \
  --meter-id 123 \
  --parameter low_balance_alarm \
  --value 100 \
  --confirm-meter-number ABC123 \
  --user-id 7 \
  --reason "bench pilot"
```

There is no bulk prepaid-write option.

## Observation-only activation

1. Deploy code and migrate while all feature switches remain false.
2. Change exactly one intended meter to `credit_controlled`.
3. Create a `MeterCreditAccount` linked to its current active `MeterInstallation` and active lease.
4. Configure fixed/deposit/lower-of/lease-override limit and thresholds.
5. Activate the account with an authorized user so TMS snapshots the live cumulative kWh, tariff, unpaid electricity and resolved credit limit.
6. Keep automatic cutoff/restore/notifications false.
7. Run `process_meter_credit_evaluations --meter-id <id> --dry-run`, inspect the result, then run without `--dry-run` when satisfied.
8. To enqueue automatically after future stored live readings, set only `METER_ENABLE_AUTOMATIC_CREDIT_EVALUATION=True` and schedule the processor command. This still does not enable tenant messages or relay switching.

## One-meter credit-control pilot

Only after observation results have been reconciled:

1. Set `METER_CREDIT_ALLOWED_METER_IDS=<meter_id>`.
2. Leave `METER_EMERGENCY_STOP=False` only when the pilot is actively approved.
3. Enable notifications separately if desired: `METER_ENABLE_AUTOMATIC_NOTIFICATIONS=True`.
4. For manual enforcement, keep `METER_ENABLE_AUTOMATIC_CUTOFF=False` and use the permission-controlled approval path/queue.
5. For automatic pilot, set the account's `automatic_cutoff=True`, `manual_only_cutoff=False`, then set `METER_ENABLE_AUTOMATIC_CUTOFF=True`.
6. Enable automatic restoration independently with the account's `automatic_restore=True` and `METER_ENABLE_AUTOMATIC_RESTORE=True`.
7. Protected hours default to 20:00-08:00. Queued OFF commands are revalidated immediately before transport.
8. Run `process_meter_commands` periodically as a fallback to reconnect-triggered dispatch.

Emergency stop: set `METER_EMERGENCY_STOP=True`. This blocks automatic cutoff/restore even if their individual switches remain true. Manual commands keep the existing permission/safety path.

## Notification mute versus enforcement hold

They are independent. A mute suppresses automatic tenant credit reminders while exposure and enforcement continue. A hold prevents automatic OFF commands while calculations and notifications continue. Creating a hold cancels unsent automatic OFF commands; it does not cancel manual OFF commands. Releasing a hold immediately reevaluates the account.

## Offline command behavior and payment races

An offline meter leaves the durable command in `waiting_online`; this is not a terminal error. When the listener registers a meter again, waiting commands are made eligible. Immediately before transport the worker revalidates the account, lease, installation, exposure, hold, feature switches, expiry and newer manual command state. Payments trigger account reevaluation; a recalculation below the cutoff cancels an unsent automatic OFF so TMS does not send OFF followed by ON after reconnection.

## DL/T645 acknowledgement and relay verification gap

The hardened queue distinguishes socket send, protocol acknowledgement and final verification. However, this snapshot does not contain manufacturer proof of the relay-status query DI/bit mapping. Therefore automated write switches remain disabled by default and the listener does **not** promote an enforcement command to `verified` merely because bytes were written or because current/power fell. The existing `Meter.is_cutoff` heuristic is retained for compatibility but is not treated as authoritative enforcement verification.

To finish authoritative relay verification, obtain the manufacturer document that defines:

- relay/status query DI and request control code;
- response control code and byte order;
- exact ON/OFF status bit/value;
- acknowledgement/error semantics for the relay write;
- model/firmware applicability.

## Prepaid read-only pilot

1. Set exactly one meter to `prepaid_pilot`; this is not the legacy `prepaid` value.
2. Add its database ID to `METER_PREPAID_ALLOWED_METER_IDS`.
3. Set `METER_ENABLE_PREPAID_READS=True`; leave writes false.
4. Run `read_prepaid_parameters --meter-id <id>` and compare captured values with the physical meter display.

The implementation stores the repository-proven live balance/overdraft data and explicitly marks manufacturer fields whose DI is not evidenced in this snapshot as unsupported. It does not invent DIs.

## Prepaid writes and recharge

`METER_ENABLE_PREPAID_WRITES` defaults false and an allowlist is mandatory. The write service additionally requires `prepaid_pilot` mode, an exact meter-number confirmation, a permitted user and an audit reason. Because the required manufacturer command/read-back details are absent, it currently records a safe failed attempt and sends no bytes.

Recharge is likewise disabled. To enable it safely, manufacturer documentation must define the exact purchase/recharge DI/control code, data encoding/encryption/password requirements, purchase sequence semantics, acknowledgement/error response, balance readback, duplicate/replay behavior and firmware applicability. An uncertain recharge must never be automatically resent.

## Deployment

The repository's `service file name.txt` identifies the Windows service names `DjangoTenantService`, `MeterListenerService`, and `TenantMgmtService`. Do not invent a Linux systemd service name for this snapshot.

For the current Windows/NSSM-style deployment, after pulling the tested commit:

```powershell
git pull
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check
net stop DjangoTenantService
net start DjangoTenantService
net stop MeterListenerService
net start MeterListenerService
sc.exe query DjangoTenantService
sc.exe query MeterListenerService
```

If production is actually running under Linux/systemd, first identify the real unit (for example with `systemctl list-units --type=service | grep -i tenant`) and then use that exact unit; do not substitute a guessed `<actual-tms-service>`.

## Rollback

1. Immediately set `METER_EMERGENCY_STOP=True`, `METER_ENABLE_AUTOMATIC_CUTOFF=False`, `METER_ENABLE_AUTOMATIC_RESTORE=False`, and `METER_ENABLE_PREPAID_WRITES=False`.
2. Stop the scheduled evaluation/command processors if configured.
3. Cancel only pending **automatic** relay commands after reviewing their audit reason; do not silently cancel manual staff commands.
4. Change the pilot meter back to `postpaid` only after reconciling its account/commands. Legacy `prepaid` records remain legacy.
5. Disable the credit account rather than deleting it, preserving policy and audit history.
6. Roll application code back to the prior Git commit. Database rollback should only be attempted after a backup and schema review because the migration is additive and historical audit records may now exist.
7. Restart the actual application and meter-listener services and verify normal reading ingestion plus manual relay control.

## Production checklist

- Confirm normal postpaid invoice regression tests.
- Confirm the listener stores live/history readings with all feature switches false.
- Confirm no account is enabled unintentionally.
- Confirm allowlists contain only the intended pilot meter IDs.
- Confirm security-deposit ledger values do not change when exposure changes.
- Confirm notification mute and enforcement hold independently.
- Confirm offline OFF is cancelled after qualifying payment before reconnection.
- Confirm exact manufacturer relay-state verification before enabling automatic relay writes in production.
- Keep prepaid writes/recharge disabled until manufacturer protocol documentation is available and bench-tested.

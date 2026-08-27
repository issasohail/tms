from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from smart_meter.dlt645 import parse_frame
from smart_meter.models import LiveReading, Meter
from smart_meter.utils.db_send import send_via_db
from smart_meter.utils.frames import build_read_register

THREE_PHASE_METER_NUMBERS = ("260305510019", "260305510020", "260305510021")
ENERGY_REGISTERS = (
    ("00010000", "Forward Active Energy", "forward_active_energy_kwh", "kWh"),
    ("00020000", "Reverse Active Energy", "reverse_active_energy_kwh", "kWh"),
)
PHASE_REGISTERS = (
    ("02010100", "Phase A Voltage", "voltage_a", "V"),
    ("02010200", "Phase B Voltage", "voltage_b", "V"),
    ("02010300", "Phase C Voltage", "voltage_c", "V"),
    ("02020100", "Phase A Current", "current_a", "A"),
    ("02020200", "Phase B Current", "current_b", "A"),
    ("02020300", "Phase C Current", "current_c", "A"),
    ("02030000", "Total Active Power", "total_power", "kW"),
    ("02030100", "Phase A Active Power", "power_a", "kW"),
    ("02030200", "Phase B Active Power", "power_b", "kW"),
    ("02030300", "Phase C Active Power", "power_c", "kW"),
)
BULK_COMPARE_FIELDS = {
    "02010100": "voltage_a", "02010200": "voltage_b", "02010300": "voltage_c",
    "02020100": "current_a", "02020200": "current_b", "02020300": "current_c",
    "02030000": "total_power", "02030100": "power_a", "02030200": "power_b",
    "02030300": "power_c", "00010000": "total_energy",
}
BULK_FRESHNESS = timedelta(minutes=5)


class Command(BaseCommand):
    help = "Safely query allowlisted active-energy and three-phase read registers."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--meter")
        target.add_argument("--all-three", action="store_true")
        parser.add_argument("--persist", action="store_true")
        parser.add_argument("--confirm-persist", action="store_true")
        parser.add_argument("--timeout", type=float, default=8.0)
        parser.add_argument("--include-phases", action="store_true")
        parser.add_argument("--compare-bulk", action="store_true")

    def handle(self, *args, **options):
        timeout = float(options["timeout"])
        if not 1.0 <= timeout <= 30.0:
            raise CommandError("--timeout must be between 1 and 30 seconds")
        if options["persist"] != options["confirm_persist"]:
            raise CommandError("Use --persist and --confirm-persist together")
        meter_numbers = THREE_PHASE_METER_NUMBERS if options["all_three"] else (options["meter"],)
        missing = [number for number in meter_numbers if not Meter.objects.filter(meter_number=number).exists()]
        if missing:
            raise CommandError("Unknown meter number(s): " + ", ".join(missing))

        failures = 0
        for meter_number in meter_numbers:
            meter = Meter.objects.get(meter_number=meter_number)
            self.stdout.write(f"METER {meter_number}")
            registers = ENERGY_REGISTERS
            if options["include_phases"]:
                if meter.reading_profile != Meter.READING_PROFILE_TOTAL_AND_PER_PHASE:
                    raise CommandError(f"Meter {meter_number} is not configured for Total + per-phase polling")
                registers += PHASE_REGISTERS
            outcomes = {}
            for di, label, field_name, unit in registers:
                queried_at = timezone.now()
                tx = build_read_register(meter_number, di)
                result = send_via_db(
                    meter_number=meter_number, frame_hex=tx.hex().upper(), timeout=timeout,
                    expect_di=di, initiated_by="query_energy_registers",
                    reason=f"Read-only {label} query", command_type="read",
                    source="energy_probe_persist" if options["persist"] else "energy_probe",
                )
                raw_reply = result.get("reply") or ""
                try:
                    parsed = parse_frame(bytes.fromhex(raw_reply)) if raw_reply else None
                except ValueError:
                    parsed = None
                status, error, value = "timeout", result.get("error") or "", None
                returned_di = (parsed or {}).get("di", "")
                control = (parsed or {}).get("control_code")
                checksum_style = (parsed or {}).get("cs_style", "not checked")
                if parsed and parsed.get("meter_number") != meter_number:
                    status, error = "invalid", "response meter does not match request"
                elif parsed and control == 0xD1:
                    status, error = "unsupported", "meter returned negative read response C=0xD1"
                elif parsed and (control != 0x91 or returned_di != di):
                    status, error = "invalid", "response control code or DI does not match request"
                elif parsed:
                    value = (parsed.get("data") or {}).get(field_name)
                    if isinstance(value, Decimal):
                        status, error = "supported", ""
                    else:
                        status, error = "invalid", "response payload is missing or invalid BCD"
                elif result.get("ok"):
                    status, error = "invalid", "reply is not a valid DL/T645 frame"

                outcomes[di] = {"status": status, "value": value, "unit": unit}
                self.stdout.write(f"  DI {di} — {label}")
                self.stdout.write(f"    queried: {timezone.localtime(queried_at).isoformat()}")
                self.stdout.write(f"    status: {status}")
                self.stdout.write(f"    raw TX: {tx.hex().upper()}")
                self.stdout.write(f"    raw RX: {raw_reply or '<none>'}")
                control_label = f"0x{control:02X}" if control is not None else "<none>"
                self.stdout.write(f"    response control: {control_label}")
                self.stdout.write(f"    returned DI: {returned_di or '<none>'}")
                self.stdout.write(f"    checksum: {checksum_style}")
                self.stdout.write(f"    value: {value if value is not None else '<none>'} {unit}")
                if error:
                    self.stdout.write(f"    error: {error}")
                if status not in {"supported", "unsupported"}:
                    failures += 1
            self._summary(meter, outcomes, options["compare_bulk"])
        if failures:
            raise CommandError(f"{failures} register queries failed or were invalid")

    def _summary(self, meter, outcomes, compare_bulk):
        self.stdout.write("  SUMMARY")
        reverse = outcomes.get("00020000", {})
        if reverse.get("status") == "supported" and reverse.get("value") == Decimal("0.00"):
            self.stdout.write("    reverse energy: register responded; zero does not prove accumulation")
        else:
            self.stdout.write(f"    reverse energy: {reverse.get('status', 'not queried')}")
        phases = [outcomes.get(di, {}) for di in ("02030100", "02030200", "02030300")]
        total = outcomes.get("02030000", {})
        if total.get("status") == "supported" and all(row.get("status") == "supported" for row in phases):
            phase_sum = sum((row["value"] for row in phases), Decimal("0"))
            self.stdout.write(f"    total power {total['value']} kW; phase sum {phase_sum} kW; delta {total['value'] - phase_sum} kW")
        if not compare_bulk:
            return
        try:
            live = meter.live
        except LiveReading.DoesNotExist:
            live = None
        if not live or timezone.now() - live.ts > BULK_FRESHNESS:
            self.stdout.write("    bulk comparison: unavailable or older than five minutes")
            return
        self.stdout.write(f"    bulk timestamp: {timezone.localtime(live.ts).isoformat()} (timestamps differ; load variation is expected)")
        for di, result in outcomes.items():
            field = BULK_COMPARE_FIELDS.get(di)
            bulk_value = getattr(live, field, None) if field else None
            if result.get("status") == "supported" and bulk_value is not None:
                bulk_value = Decimal(bulk_value)
                self.stdout.write(f"    {di}: direct {result['value']} vs bulk {bulk_value}; delta {result['value'] - bulk_value} {result['unit']}")

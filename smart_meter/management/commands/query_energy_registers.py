from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from smart_meter.dlt645 import parse_frame
from smart_meter.models import Meter
from smart_meter.utils.db_send import send_via_db
from smart_meter.utils.frames import build_read_register


THREE_PHASE_METER_NUMBERS = (
    "260305510019",
    "260305510020",
    "260305510021",
)
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


class Command(BaseCommand):
    help = "Safely query forward and reverse cumulative active-energy registers."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--meter", help="Query one meter number.")
        target.add_argument(
            "--all-three",
            action="store_true",
            help="Query the three approved three-phase meters.",
        )
        parser.add_argument(
            "--persist",
            action="store_true",
            help="Persist replies after the listener validates checksum, meter number, DI, and BCD.",
        )
        parser.add_argument("--timeout", type=float, default=8.0)
        parser.add_argument(
            "--include-phases",
            action="store_true",
            help="Also query the documented voltage/current/active-power DIs sequentially.",
        )

    def handle(self, *args, **options):
        timeout = options["timeout"]
        if timeout <= 0:
            raise CommandError("--timeout must be greater than zero")

        meter_numbers = (
            THREE_PHASE_METER_NUMBERS if options["all_three"] else (options["meter"],)
        )
        missing = [
            number
            for number in meter_numbers
            if not Meter.objects.filter(meter_number=number).exists()
        ]
        if missing:
            raise CommandError("Unknown meter number(s): " + ", ".join(missing))

        failures = 0
        for meter_number in meter_numbers:
            self.stdout.write(f"Meter {meter_number}")
            meter = Meter.objects.get(meter_number=meter_number)
            registers = ENERGY_REGISTERS
            if options["include_phases"]:
                if meter.reading_profile != Meter.READING_PROFILE_TOTAL_AND_PER_PHASE:
                    raise CommandError(
                        f"Meter {meter_number} is not configured for Total + per-phase polling"
                    )
                registers += PHASE_REGISTERS
            for di, label, field_name, unit in registers:
                tx = build_read_register(meter_number, di)
                self.stdout.write(f"  TX {tx.hex().upper()}")
                result = send_via_db(
                    meter_number=meter_number,
                    frame_hex=tx.hex().upper(),
                    timeout=timeout,
                    expect_di=di,
                    initiated_by="query_energy_registers",
                    reason=f"Read-only {label} query",
                    command_type="read",
                    source="energy_probe_persist" if options["persist"] else "energy_probe",
                )
                if not result.get("ok"):
                    failures += 1
                    self.stderr.write(
                        f"  {label}: {result.get('error') or result.get('status') or 'timed out'}"
                    )
                    continue

                raw_reply = result.get("reply") or ""
                self.stdout.write(f"  RX {raw_reply}")
                try:
                    frame = bytes.fromhex(raw_reply)
                except ValueError:
                    parsed = None
                else:
                    parsed = parse_frame(frame)
                value = (parsed.get("data") or {}).get(field_name) if parsed else None
                if (
                    not parsed
                    or parsed.get("meter_number") != meter_number
                    or parsed.get("di") != di
                    or not isinstance(value, Decimal)
                ):
                    failures += 1
                    self.stderr.write(f"  {label}: invalid or mismatched reply")
                    continue
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  DI {di} {label} = {value} {unit}"
                        + (" (persisted)" if options["persist"] else " (not persisted)")
                    )
                )

        if failures:
            raise CommandError(f"{failures} register queries failed")

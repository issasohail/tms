"""Query allowlisted DL/T645 registers through the running listener only."""

from __future__ import annotations

from decimal import Decimal
import json
import socket

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from smart_meter.diagnostic import (
    DIAGNOSTIC_DI_ALLOWLIST,
    DIAGNOSTIC_REGISTERS,
    decode_diagnostic_response,
    normalize_diagnostic_di,
    validate_meter_number,
)


DEFAULT_SOCKET = str(
    getattr(settings, "METER_DIAGNOSTIC_SOCKET", "/tmp/tms-meter-diagnostic.sock")
)


def call_diagnostic_listener(path: str, request: dict, timeout: float) -> dict:
    payload = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout + 2.0)
        client.connect(path)
        client.sendall(payload)
        client.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw:
        raise CommandError("listener returned an empty diagnostic response")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommandError("listener returned invalid diagnostic JSON") from exc


def _display_value(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return json.dumps(value, default=_display_value, sort_keys=True)
    return "" if value is None else str(value)


class Command(BaseCommand):
    help = (
        "Read allowlisted DL/T645 DIs through the running listener's existing "
        "meter connections without database persistence."
    )

    def add_arguments(self, parser):
        parser.add_argument("--meter", action="append", required=True)
        parser.add_argument(
            "--di",
            action="append",
            help="Allowlisted DI; repeat for multiple registers. Defaults to the full allowlist.",
        )
        parser.add_argument("--timeout", type=float, default=8.0)
        parser.add_argument("--socket", default=DEFAULT_SOCKET)

    def handle(self, *args, **options):
        timeout = float(options["timeout"])
        if not 1.0 <= timeout <= 30.0:
            raise CommandError("--timeout must be between 1 and 30 seconds")
        try:
            meters = [validate_meter_number(value) for value in options["meter"]]
            dis = (
                [normalize_diagnostic_di(value) for value in options["di"]]
                if options.get("di")
                else list(DIAGNOSTIC_REGISTERS)
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        if len(set(meters)) != len(meters):
            raise CommandError("duplicate --meter values are not permitted")
        if len(set(dis)) != len(dis):
            raise CommandError("duplicate --di values are not permitted")
        if not set(dis) <= DIAGNOSTIC_DI_ALLOWLIST:
            raise CommandError("one or more DIs are not allowlisted")

        for meter in meters:
            self.stdout.write(f"METER {meter}")
            outcomes = {}
            for di in dis:
                request = {"meter": meter, "di": di, "timeout": timeout}
                try:
                    transport = call_diagnostic_listener(
                        options["socket"], request, timeout
                    )
                except (OSError, CommandError) as exc:
                    outcomes[di] = {"status": "invalid_response", "value": None}
                    self._print_result(
                        meter=meter, di=di, status="invalid_response",
                        raw_tx="", raw_rx="", returned_di="", checksum="not checked",
                        value=None, unit=DIAGNOSTIC_REGISTERS[di].unit, error=str(exc),
                    )
                    continue

                raw_tx = transport.get("tx", "")
                raw_rx = transport.get("rx", "")
                if not transport.get("ok"):
                    outcomes[di] = {
                        "status": transport.get("status", "invalid_response"),
                        "value": None,
                    }
                    self._print_result(
                        meter=meter, di=di, status=transport.get("status", "invalid_response"),
                        raw_tx=raw_tx, raw_rx=raw_rx, returned_di="",
                        checksum="not checked", value=None,
                        unit=DIAGNOSTIC_REGISTERS[di].unit,
                        error=transport.get("error", "listener rejected request"),
                    )
                    continue
                try:
                    decoded = decode_diagnostic_response(
                        bytes.fromhex(raw_rx), expected_meter=meter, expected_di=di
                    )
                except (ValueError, TypeError) as exc:
                    outcomes[di] = {"status": "invalid_response", "value": None}
                    self._print_result(
                        meter=meter, di=di, status="invalid_response",
                        raw_tx=raw_tx, raw_rx=raw_rx, returned_di="",
                        checksum="failed or invalid", value=None,
                        unit=DIAGNOSTIC_REGISTERS[di].unit, error=str(exc),
                    )
                    continue
                outcomes[di] = decoded
                self._print_result(
                    meter=meter,
                    di=di,
                    status=decoded["status"],
                    raw_tx=raw_tx,
                    raw_rx=raw_rx,
                    returned_di=decoded["returned_di"],
                    checksum=(
                        f"ok ({decoded['checksum_style']})"
                        if decoded["checksum_ok"] else "failed"
                    ),
                    value=decoded["value"],
                    unit=decoded["unit"],
                    error=decoded["error"],
                )
            self._print_comparison(outcomes)

    def _print_result(
        self, *, meter, di, status, raw_tx, raw_rx, returned_di,
        checksum, value, unit, error,
    ):
        self.stdout.write(f"  DI {di} — {DIAGNOSTIC_REGISTERS[di].label}")
        self.stdout.write(f"    status: {status}")
        self.stdout.write(f"    raw TX: {raw_tx or '<not transmitted>'}")
        self.stdout.write(f"    raw RX: {raw_rx or '<none>'}")
        self.stdout.write(f"    returned DI: {returned_di or '<none>'}")
        self.stdout.write(f"    checksum: {checksum}")
        self.stdout.write(f"    value: {_display_value(value) or '<none>'}")
        self.stdout.write(f"    unit: {_display_value(unit) or '<none>'}")
        if error:
            self.stdout.write(f"    error: {error}")

    def _print_comparison(self, outcomes):
        self.stdout.write("  COMPARISON")
        direct_supported = [
            di for di, result in outcomes.items() if result.get("status") == "supported"
        ]
        self.stdout.write(
            "    supported direct DIs: "
            + (", ".join(di for di in direct_supported if di != "028011FF") or "<none>")
        )
        reverse = outcomes.get("00020000", {})
        if reverse.get("status") == "supported":
            note = "register responded"
            if reverse.get("value") == Decimal("0.00"):
                note += "; zero does not prove reverse accumulation"
            self.stdout.write(f"    reverse active energy: {note}")
        else:
            self.stdout.write(
                f"    reverse active energy: {reverse.get('status', 'not queried')}"
            )

        total = outcomes.get("02030000", {})
        phases = [outcomes.get(di, {}) for di in ("02030100", "02030200", "02030300")]
        if total.get("status") == "supported" and all(
            item.get("status") == "supported" for item in phases
        ):
            phase_sum = sum((item["value"] for item in phases), Decimal("0"))
            delta = total["value"] - phase_sum
            self.stdout.write(
                f"    direct total power: {total['value']} kW; phase sum: "
                f"{phase_sum} kW; delta: {delta} kW"
            )
        else:
            self.stdout.write("    direct total/phase power agreement: insufficient data")

        bulk = outcomes.get("028011FF", {})
        bulk_values = bulk.get("value") if bulk.get("status") == "supported" else None
        if not isinstance(bulk_values, dict):
            self.stdout.write("    direct versus 028011FF: insufficient data")
            return
        pairs = (
            ("02010100", "voltage_a", "V"), ("02010200", "voltage_b", "V"),
            ("02010300", "voltage_c", "V"), ("02020100", "current_a", "A"),
            ("02020200", "current_b", "A"), ("02020300", "current_c", "A"),
            ("02030000", "total_power", "kW"), ("02030100", "power_a", "kW"),
            ("02030200", "power_b", "kW"), ("02030300", "power_c", "kW"),
            ("00010000", "total_energy", "kWh"),
        )
        compared = False
        for di, bulk_name, unit in pairs:
            direct = outcomes.get(di, {})
            if direct.get("status") != "supported" or bulk_name not in bulk_values:
                continue
            compared = True
            delta = direct["value"] - bulk_values[bulk_name]
            self.stdout.write(
                f"    {di} vs bulk {bulk_name}: {direct['value']} vs "
                f"{bulk_values[bulk_name]} {unit}; delta {delta} {unit}"
            )
        if not compared:
            self.stdout.write("    direct versus 028011FF: insufficient data")

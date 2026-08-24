"""Authoritative relay-state synchronization and command verification."""

import re

from django.db import transaction
from django.utils import timezone

from smart_meter.dlt645 import relay_state_from_status_word
from smart_meter.models import Meter, MeterCommand


STATUS_WORD_RE = re.compile(r"^[0-9A-Fa-f]{4}$")


def classify_relay_ack(parsed, meter_number):
    """Classify only a relay ACK belonging to the expected meter."""
    if not parsed or parsed.get("meter_number") != meter_number:
        return None
    control = parsed.get("control_code")
    if control == 0x9C:
        return "acknowledged"
    if control == 0xDC:
        return "failed"
    return None


def parse_authoritative_relay_state(status_word):
    """Return ``on``/``off`` only for a valid two-byte 0x028011FF word."""
    normalized = str(status_word or "").strip().removeprefix("0x").removeprefix("0X")
    if not STATUS_WORD_RE.fullmatch(normalized):
        return None
    return relay_state_from_status_word(normalized)


def sync_authoritative_relay_status(
    meter,
    status_word,
    *,
    command=None,
    status_reply_hex="",
    received_at=None,
):
    """Synchronize cached state and optionally verify one correlated command.

    Missing or malformed status never changes the meter or a previously
    confirmed command state.  A mismatched readback is recorded but remains
    acknowledged rather than being promoted to verified.
    """
    relay_state = parse_authoritative_relay_state(status_word)
    if relay_state is None:
        return None

    received_at = received_at or timezone.now()
    with transaction.atomic():
        locked_meter = Meter.objects.select_for_update().get(pk=meter.pk)
        if locked_meter.power_status != relay_state:
            locked_meter.power_status = relay_state
            locked_meter.save(update_fields=["power_status"])

        if command is not None:
            locked_command = MeterCommand.objects.select_for_update().get(pk=command.pk)
            if locked_command.meter_id != locked_meter.pk:
                raise ValueError("relay status response meter does not match command meter")
            if locked_command.command_type != "relay":
                raise ValueError("relay status cannot verify a non-relay command")

            locked_command.parsed_relay_state = relay_state
            locked_command.reply_hex = status_reply_hex or locked_command.reply_hex
            update_fields = ["parsed_relay_state", "reply_hex", "updated_at"]
            if relay_state == locked_command.desired_state:
                locked_command.status = "verified"
                locked_command.verified_at = received_at
                locked_command.error = ""
                update_fields.extend(["status", "verified_at", "error"])
            else:
                locked_command.status = "acknowledged"
                locked_command.error = (
                    "acknowledged but not verified: physical relay state "
                    f"{relay_state} does not match requested {locked_command.desired_state}"
                )
                update_fields.extend(["status", "error"])
            locked_command.save(update_fields=update_fields)

    meter.power_status = relay_state
    return relay_state

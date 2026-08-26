"""Authoritative relay-state synchronization and command verification."""

import re
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from smart_meter.dlt645 import relay_state_from_status_word
from smart_meter.models import Meter, MeterCommand


STATUS_WORD_RE = re.compile(r"^[0-9A-Fa-f]{4}$")
LIVE_RELAY_ACTIVE_STATUSES = frozenset(
    {"new", "pending", "waiting_online", "claimed", "sent", "retry"}
)
LIVE_RELAY_FAILURE_STATUSES = frozenset({"failed", "error", "timeout", "expired"})
LIVE_RELAY_FAILURE_DISPLAY_AGE = timedelta(minutes=5)


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


def reconcile_live_relay_command_state(
    meter,
    command,
    status_word,
    reading_at,
    *,
    is_fresh,
    now=None,
):
    """Return the relay-command state suitable for the live page.

    A fresh authoritative reading may complete an acknowledged command only
    when it was recorded after that command and its relay state matches the
    requested state. ACK alone, connectivity, and electrical usage are never
    treated as physical verification.
    """
    now = now or timezone.now()
    confirmed_state = parse_authoritative_relay_state(status_word)

    if (
        command is not None
        and command.status == "acknowledged"
        and is_fresh
        and confirmed_state == command.desired_state
        and reading_at is not None
        and reading_at >= command.created_at
    ):
        sync_authoritative_relay_status(
            meter,
            status_word,
            command=command,
            received_at=reading_at,
        )
        command.refresh_from_db()

    status = command.status if command is not None else ""
    error = command.error if command is not None else ""

    if command is not None and status in LIVE_RELAY_ACTIVE_STATUSES:
        if command.expires_at and command.expires_at <= now:
            if now <= command.expires_at + LIVE_RELAY_FAILURE_DISPLAY_AGE:
                status = "expired"
                error = error or "Relay command expired"
            else:
                status = ""
                error = ""
    elif command is not None and status in LIVE_RELAY_FAILURE_STATUSES:
        if command.updated_at < now - LIVE_RELAY_FAILURE_DISPLAY_AGE:
            status = ""
            error = ""

    operation_label = ""
    if status in LIVE_RELAY_ACTIVE_STATUSES:
        operation_label = "Restoring…" if command.desired_state == "on" else "Connecting…"
    indicator_label = operation_label
    indicator_class = "is-working" if operation_label else ""
    if status in LIVE_RELAY_FAILURE_STATUSES:
        indicator_label = "Failed"
        indicator_class = "is-error"

    return {
        "confirmed_state": confirmed_state,
        "status": status,
        "desired_state": command.desired_state if command is not None else "",
        "error": error,
        "operation_label": operation_label,
        "indicator_label": indicator_label,
        "indicator_class": indicator_class,
    }

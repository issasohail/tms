"""Resolve global and per-meter connection-event capture policy."""

from __future__ import annotations

import threading
import time

from django.db import DatabaseError
from django.utils import timezone

from smart_meter.models import Meter, MeterSettings


_CACHE_SECONDS = 30
_cache_lock = threading.Lock()
_global_cache = {"expires": 0.0, "mode": MeterSettings.CONNECTION_EVENT_CAPTURE_OFF}
_meter_cache = {}


def clear_connection_event_capture_cache():
    with _cache_lock:
        _global_cache.update(
            expires=0.0,
            mode=MeterSettings.CONNECTION_EVENT_CAPTURE_OFF,
        )
        _meter_cache.clear()


def _global_mode(now_monotonic):
    if _global_cache["expires"] <= now_monotonic:
        mode = (
            MeterSettings.objects.filter(pk=1)
            .values_list("connection_event_capture_mode", flat=True)
            .first()
            or MeterSettings.CONNECTION_EVENT_CAPTURE_OFF
        )
        _global_cache.update(
            expires=now_monotonic + _CACHE_SECONDS,
            mode=mode,
        )
    return _global_cache["mode"]


def effective_connection_event_capture_mode(meter_number, *, at=None):
    at = at or timezone.now()
    now_monotonic = time.monotonic()
    key = str(meter_number or "").strip()
    with _cache_lock:
        try:
            global_mode = _global_mode(now_monotonic)
            cached = _meter_cache.get(key)
            if not cached or cached["expires"] <= now_monotonic:
                policy = (
                    Meter.objects.filter(meter_number=key)
                    .values("connection_event_capture_mode", "connection_event_capture_until")
                    .first()
                )
                cached = {
                    "expires": now_monotonic + _CACHE_SECONDS,
                    "mode": (
                        policy["connection_event_capture_mode"]
                        if policy else Meter.CONNECTION_EVENT_CAPTURE_INHERIT
                    ),
                    "until": policy["connection_event_capture_until"] if policy else None,
                }
                _meter_cache[key] = cached
        except DatabaseError:
            return MeterSettings.CONNECTION_EVENT_CAPTURE_OFF

    mode = cached["mode"]
    if (
        mode == Meter.CONNECTION_EVENT_CAPTURE_ALL
        and cached["until"]
        and cached["until"] <= at
    ):
        mode = Meter.CONNECTION_EVENT_CAPTURE_INHERIT
    if mode == Meter.CONNECTION_EVENT_CAPTURE_INHERIT:
        return global_mode
    return mode


def should_capture_connection_event(meter_number, *, at=None):
    return effective_connection_event_capture_mode(
        meter_number,
        at=at,
    ) == Meter.CONNECTION_EVENT_CAPTURE_ALL

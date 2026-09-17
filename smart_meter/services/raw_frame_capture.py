"""Resolve global and per-meter raw-frame capture policy."""

from __future__ import annotations

import threading
import time

from django.db import DatabaseError
from django.utils import timezone

from smart_meter.models import Meter, MeterSettings


_CACHE_SECONDS = 30
_cache_lock = threading.Lock()
_global_cache = {"expires": 0.0, "mode": MeterSettings.RAW_FRAME_CAPTURE_ERRORS}


def clear_raw_frame_capture_cache():
    """Test/admin hook; listener processes otherwise refresh every 30 seconds."""
    with _cache_lock:
        _global_cache.update(expires=0.0, mode=MeterSettings.RAW_FRAME_CAPTURE_ERRORS)


def global_raw_frame_capture_mode():
    now_monotonic = time.monotonic()
    with _cache_lock:
        if _global_cache["expires"] > now_monotonic:
            return _global_cache["mode"]
        try:
            mode = (
                MeterSettings.objects.filter(pk=1)
                .values_list("raw_frame_capture_mode", flat=True)
                .first()
                or MeterSettings.RAW_FRAME_CAPTURE_ERRORS
            )
        except DatabaseError:
            # Fail safely without turning high-volume capture back on.
            mode = MeterSettings.RAW_FRAME_CAPTURE_ERRORS
        _global_cache.update(expires=now_monotonic + _CACHE_SECONDS, mode=mode)
        return mode


def effective_raw_frame_capture_mode(meter: Meter, *, at=None):
    at = at or timezone.now()
    mode = meter.raw_frame_capture_mode or Meter.RAW_FRAME_CAPTURE_INHERIT
    expires = meter.raw_frame_capture_until
    if mode == Meter.RAW_FRAME_CAPTURE_ALL and expires and expires <= at:
        mode = Meter.RAW_FRAME_CAPTURE_INHERIT
    if mode == Meter.RAW_FRAME_CAPTURE_INHERIT:
        return global_raw_frame_capture_mode()
    return mode


def should_capture_raw_frame(meter: Meter, *, is_error=False, at=None):
    mode = effective_raw_frame_capture_mode(meter, at=at)
    if mode == Meter.RAW_FRAME_CAPTURE_ALL:
        return True
    if mode == Meter.RAW_FRAME_CAPTURE_ERRORS:
        return bool(is_error)
    return False

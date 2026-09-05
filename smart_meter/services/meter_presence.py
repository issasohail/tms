from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from functools import lru_cache
from typing import Iterable

from django.conf import settings
from django.utils import timezone
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry


logger = logging.getLogger(__name__)
KEY_PREFIX = "smart_meter:presence:"
_FAILURE_RETRY_SECONDS = 5.0
_failure_lock = threading.Lock()
_retry_after_monotonic = 0.0


@dataclass(frozen=True)
class MeterPresence:
    available: bool
    connected: bool = False
    last_contact_at: datetime | None = None
    source_ip: str | None = None
    source_port: int | None = None
    connection_identity: str | None = None
    connection_generation: int | None = None


def presence_ttl_seconds() -> int:
    configured = getattr(settings, "SMART_METER_PRESENCE_TTL_SECONDS", None)
    if configured is not None:
        return max(1, int(configured))
    freshness_seconds = int(settings.SMART_METER_ONLINE_THRESHOLD_MINUTES) * 60
    return max(60, freshness_seconds * 2)


def _configured_redis_url() -> str | None:
    """Use an existing application Redis setting; never invent connection details."""
    for setting_name in (
        "SMART_METER_REDIS_URL",
        "BILLING_RQ_REDIS_URL",
        "CELERY_BROKER_URL",
    ):
        value = (getattr(settings, setting_name, "") or "").strip()
        if value.startswith(("redis://", "rediss://", "unix://")):
            return value
    return None


@lru_cache(maxsize=1)
def _get_redis_client() -> Redis | None:
    redis_url = _configured_redis_url()
    if not redis_url:
        return None
    return Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=0.1,
        socket_timeout=0.1,
        health_check_interval=30,
        retry=Retry(NoBackoff(), 0),
    )


def _redis_is_in_backoff() -> bool:
    with _failure_lock:
        return time.monotonic() < _retry_after_monotonic


def _note_redis_failure(exc: Exception) -> None:
    global _retry_after_monotonic
    with _failure_lock:
        should_log = time.monotonic() >= _retry_after_monotonic
        _retry_after_monotonic = time.monotonic() + _FAILURE_RETRY_SECONDS
    if should_log:
        logger.warning("Smart-meter presence Redis unavailable; using reading fallback: %s", exc)


def _key(meter_number: str) -> str:
    return f"{KEY_PREFIX}{str(meter_number).strip()}"


def _as_presence(values) -> MeterPresence:
    if not values:
        return MeterPresence(available=True)
    try:
        epoch = float(values.get("last_contact_at", ""))
        last_contact_at = datetime.fromtimestamp(epoch, tz=datetime_timezone.utc)
    except (TypeError, ValueError, OSError):
        last_contact_at = None
    try:
        source_port = int(values["source_port"]) if values.get("source_port") else None
    except (TypeError, ValueError):
        source_port = None
    try:
        generation = int(values["connection_generation"]) if values.get("connection_generation") else None
    except (TypeError, ValueError):
        generation = None
    return MeterPresence(
        available=True,
        connected=values.get("connected") == "1",
        last_contact_at=last_contact_at,
        source_ip=values.get("source_ip") or None,
        source_port=source_port,
        connection_identity=values.get("connection_identity") or None,
        connection_generation=generation,
    )


def record_meter_contact(
    meter_number,
    source_ip=None,
    source_port=None,
    *,
    connection_identity=None,
    connection_generation=None,
) -> bool:
    """Record valid contact and refresh its TTL without allowing an old socket to win."""
    if not meter_number or _redis_is_in_backoff():
        return False
    client = _get_redis_client()
    if client is None:
        return False
    identity = str(connection_identity or "")
    generation = int(connection_generation or time.time_ns())
    now_epoch = f"{timezone.now().timestamp():.6f}"
    script = """
local current_generation = tonumber(redis.call('HGET', KEYS[1], 'connection_generation') or '0')
local current_identity = redis.call('HGET', KEYS[1], 'connection_identity') or ''
local incoming_generation = tonumber(ARGV[1])
if current_generation > incoming_generation and current_identity ~= ARGV[2] then
    return 0
end
redis.call('HSET', KEYS[1],
    'connection_generation', ARGV[1],
    'connection_identity', ARGV[2],
    'connected', '1',
    'last_contact_at', ARGV[3],
    'source_ip', ARGV[4],
    'source_port', ARGV[5])
redis.call('EXPIRE', KEYS[1], ARGV[6])
return 1
"""
    try:
        return bool(
            client.eval(
                script,
                1,
                _key(meter_number),
                generation,
                identity,
                now_epoch,
                source_ip or "",
                source_port if source_port is not None else "",
                presence_ttl_seconds(),
            )
        )
    except (RedisError, OSError, ValueError) as exc:
        _note_redis_failure(exc)
        return False


def clear_meter_connection(meter_number, connection_identity=None) -> bool:
    """Clear only the socket identity that still owns the presence record."""
    if not meter_number or _redis_is_in_backoff():
        return False
    client = _get_redis_client()
    if client is None:
        return False
    script = """
local current_identity = redis.call('HGET', KEYS[1], 'connection_identity') or ''
if ARGV[1] ~= '' and current_identity ~= ARGV[1] then
    return 0
end
if redis.call('EXISTS', KEYS[1]) == 0 then
    return 0
end
redis.call('HSET', KEYS[1], 'connected', '0')
return 1
"""
    try:
        return bool(client.eval(script, 1, _key(meter_number), connection_identity or ""))
    except (RedisError, OSError, ValueError) as exc:
        _note_redis_failure(exc)
        return False


def get_meter_presence(meter_number) -> MeterPresence:
    return get_meter_presences([meter_number]).get(
        str(meter_number), MeterPresence(available=False)
    )


def get_meter_presences(meter_numbers: Iterable[str]) -> dict[str, MeterPresence]:
    numbers = [str(number).strip() for number in meter_numbers if str(number).strip()]
    if not numbers:
        return {}
    if _redis_is_in_backoff():
        return {number: MeterPresence(available=False) for number in numbers}
    client = _get_redis_client()
    if client is None:
        return {number: MeterPresence(available=False) for number in numbers}
    try:
        pipe = client.pipeline(transaction=False)
        for number in numbers:
            pipe.hgetall(_key(number))
        return {
            number: _as_presence(values)
            for number, values in zip(numbers, pipe.execute())
        }
    except (RedisError, OSError, ValueError) as exc:
        _note_redis_failure(exc)
        return {number: MeterPresence(available=False) for number in numbers}

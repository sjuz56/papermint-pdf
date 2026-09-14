"""Privacy-conscious Free-plan quotas shared by the web and worker processes."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from redis import Redis
from redis.exceptions import RedisError


FREE_DAILY_TASK_LIMIT = max(1, int(os.getenv("PAPERMINT_FREE_DAILY_TASK_LIMIT", "2")))
FREE_UPLOAD_MB = max(1, int(os.getenv("PAPERMINT_FREE_UPLOAD_MB", "10")))
FREE_UPLOAD_BYTES = FREE_UPLOAD_MB * 1024 * 1024

# A configured secret keeps anonymous network identifiers resistant to offline
# guessing. The random fallback is safe for local development, but resets its
# anonymous counters when the web process restarts.
_configured_secret = os.getenv("PAPERMINT_USAGE_HASH_SECRET", "").strip()
_hash_secret = (
    _configured_secret.encode("utf-8")
    if _configured_secret
    else secrets.token_bytes(32)
)


class FreeLimitReached(RuntimeError):
    pass


class FreeLimitUnavailable(RuntimeError):
    pass


def anonymous_quota_key(network_identifier: str) -> str:
    """Return an opaque HMAC; the IP address itself is never stored."""
    value = (network_identifier or "unknown").strip().encode("utf-8")
    digest = hmac.new(_hash_secret, b"anonymous:" + value, hashlib.sha256).hexdigest()
    return f"anon:{digest}"


def account_quota_key(user_id: str) -> str:
    digest = hmac.new(
        _hash_secret,
        b"account:" + (user_id or "unknown").encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"user:{digest}"


def _redis() -> Redis:
    return Redis.from_url(
        os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        socket_connect_timeout=3,
        socket_timeout=5,
        health_check_interval=30,
    )


def _daily_key(client_key: str, timestamp: float | None = None) -> tuple[str, int]:
    now = float(timestamp if timestamp is not None else time.time())
    day = int(now // 86400)
    # Keep the bucket slightly beyond midnight so delayed jobs can still refund it.
    ttl = max(3600, int(((day + 1) * 86400) - now) + 3600)
    return f"papermint:free-tasks:{day}:{client_key}", ttl


_RESERVE_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local limit = tonumber(ARGV[1])
if current >= limit then
  return -1
end
local updated = redis.call('INCR', KEYS[1])
if updated == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return updated
"""

_RELEASE_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current <= 1 then
  redis.call('DEL', KEYS[1])
  return 0
end
return redis.call('DECR', KEYS[1])
"""


def reserve_free_task(
    client_key: str,
    *,
    limit: int = FREE_DAILY_TASK_LIMIT,
    connection: Redis | None = None,
    timestamp: float | None = None,
) -> dict:
    key, ttl = _daily_key(client_key, timestamp)
    try:
        used = int((connection or _redis()).eval(_RESERVE_SCRIPT, 1, key, limit, ttl))
    except (RedisError, OSError, ValueError) as exc:
        raise FreeLimitUnavailable("The Free usage counter is temporarily unavailable.") from exc
    if used < 0:
        raise FreeLimitReached(
            f"The Free plan includes {limit} PDF tasks per day. Sign in and upgrade to continue."
        )
    return {"used": used, "limit": limit, "remaining": max(0, limit - used), "key": client_key}


def release_free_task(
    client_key: str | None,
    *,
    connection: Redis | None = None,
    timestamp: float | None = None,
) -> None:
    if not client_key:
        return
    key, _ = _daily_key(client_key, timestamp)
    try:
        (connection or _redis()).eval(_RELEASE_SCRIPT, 1, key)
    except (RedisError, OSError, ValueError) as exc:
        raise FreeLimitUnavailable("The Free usage counter could not be updated.") from exc

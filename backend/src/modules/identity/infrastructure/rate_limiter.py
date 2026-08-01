"""Redis-based sliding window rate limiter.

Uses Redis sorted sets under a single atomic Lua script to track requests
per key (e.g. client IP, email hash) within a configurable time window.
The login flow keeps its original ``check_rate_limit`` API while
``check_rate_limit_for`` generalizes the mechanism to arbitrary key
prefixes and limits (used by the forgot-password flow).
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass

import redis.asyncio as redis

from src.modules.identity.infrastructure.config import AuthSettings

# Atomic sliding-window check-and-record, executed server-side in one
# EVAL so concurrent bursts cannot exceed the limit (a separate read
# pipeline followed by a write pipeline would race). ARGV[4] is a
# per-request unique member so same-timestamp requests never collide in
# the sorted set.
# KEYS[1] = rate limit key; ARGV[1] = now; ARGV[2] = window seconds;
# ARGV[3] = max requests; ARGV[4] = unique member for this request.
_SLIDING_WINDOW_SCRIPT = """
local now = tonumber(ARGV[1])
local window_start = now - tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', window_start)
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[3]) then
  return 0
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
"""


@dataclass(frozen=True)
class RateLimitRule:
    """Maximum requests allowed per sliding window of ``window_seconds``."""

    max_requests: int
    window_seconds: int


def email_identifier(email: str) -> str:
    """Stable identifier for an email address: its SHA-256 hex digest.

    Used as the per-email rate limit key so the raw address never appears
    in Redis keys or logs.
    """
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


class RateLimiter:
    """Redis-based sliding window rate limiter.

    Tracks requests per key (e.g. login attempts per IP address, forgot
    password requests per IP or per email hash) using Redis sorted sets.
    Each request is stored as a member with its timestamp as the score,
    enabling efficient sliding window calculations.

    Args:
        redis_client: An async Redis client instance.
        settings: AuthSettings containing rate_limit_login_max and
            rate_limit_login_window_seconds (used as defaults by the
            backward-compatible ``check_rate_limit`` login wrapper).

    Example:
        >>> limiter = RateLimiter(redis_client, settings)
        >>> allowed = await limiter.check_rate_limit("192.168.1.1")
        >>> if not allowed:
        ...     raise RateLimitExceededError()
    """

    def __init__(self, redis_client: redis.Redis, settings: AuthSettings) -> None:
        """Initialize the rate limiter.

        Args:
            redis_client: An async Redis client instance.
            settings: AuthSettings containing rate limit configuration.
        """
        self._redis = redis_client
        self._max_requests = settings.rate_limit_login_max
        self._window_seconds = settings.rate_limit_login_window_seconds

    async def check_rate_limit(self, ip: str) -> bool:
        """Check whether a request from the given IP is within the login rate limit.

        Backward-compatible wrapper around :meth:`check_rate_limit_for` using
        the login key prefix (``rate_limit:login:{ip}``) and the login limits
        configured at construction time.

        Args:
            ip: The client IP address to check.

        Returns:
            True if the request is allowed (under the limit), False if the
            rate limit has been exceeded.
        """
        return await self.check_rate_limit_for(
            key_prefix="login",
            identifier=ip,
            rule=RateLimitRule(self._max_requests, self._window_seconds),
        )

    async def check_rate_limit_for(
        self,
        key_prefix: str,
        identifier: str,
        rule: RateLimitRule,
    ) -> bool:
        """Check whether a request for ``key_prefix``/``identifier`` is within the limit.

        Uses a sliding window algorithm with Redis sorted sets under the
        key ``rate_limit:{key_prefix}:{identifier}``. Pruning expired
        entries, counting, and recording the current request happen in a
        single atomic Lua script, so concurrent requests cannot exceed
        the limit.

        Args:
            key_prefix: Namespace for the limit (e.g. ``login``,
                ``forgot_password:ip``, ``forgot_password:email``).
            identifier: Stable value identifying the caller (e.g. client IP,
                or a SHA-256 hex digest of the email address).
            rule: Maximum requests per sliding window.

        Returns:
            True if the request is allowed (under the limit), False if the
            rate limit has been exceeded.
        """
        key = f"rate_limit:{key_prefix}:{identifier}"
        now = time.time()
        member = f"{now}:{secrets.token_hex(4)}"
        allowed = await self._redis.eval(
            _SLIDING_WINDOW_SCRIPT,
            1,
            key,
            now,
            rule.window_seconds,
            rule.max_requests,
            member,
        )
        return bool(allowed)

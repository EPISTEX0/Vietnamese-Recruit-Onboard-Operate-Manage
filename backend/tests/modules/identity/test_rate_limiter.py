"""Unit tests for Redis-based sliding window rate limiter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.identity.infrastructure.config import AuthSettings
from src.modules.identity.infrastructure.rate_limiter import (
    RateLimiter,
    RateLimitRule,
    email_identifier,
)


@pytest.fixture
def auth_settings() -> AuthSettings:
    """Create AuthSettings with default rate limit values for testing."""
    return AuthSettings(
        google_client_id="test-client-id",
        google_client_secret="test-client-secret",
        jwt_secret_key="test-jwt-secret-key-at-least-32-chars-long",
        oauth_token_encryption_key="dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcyE=",
    )


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Create a mock async Redis client."""
    return AsyncMock()


@pytest.fixture
def rate_limiter(mock_redis: AsyncMock, auth_settings: AuthSettings) -> RateLimiter:
    """Create a RateLimiter instance with mocked Redis."""
    return RateLimiter(mock_redis, auth_settings)


class TestRateLimiterInit:
    """Tests for RateLimiter initialization."""

    def test_stores_max_requests_from_settings(
        self, rate_limiter: RateLimiter, auth_settings: AuthSettings
    ) -> None:
        assert rate_limiter._max_requests == auth_settings.rate_limit_login_max

    def test_stores_window_seconds_from_settings(
        self, rate_limiter: RateLimiter, auth_settings: AuthSettings
    ) -> None:
        assert rate_limiter._window_seconds == auth_settings.rate_limit_login_window_seconds

    def test_stores_redis_client(self, rate_limiter: RateLimiter, mock_redis: AsyncMock) -> None:
        assert rate_limiter._redis is mock_redis


class TestEmailIdentifier:
    """Tests for the email identifier helper."""

    def test_returns_sha256_hex_digest(self) -> None:
        identifier = email_identifier("user@example.com")
        assert len(identifier) == 64
        assert identifier == email_identifier("user@example.com")

    def test_is_case_sensitive_and_stable(self) -> None:
        assert email_identifier("A@b.com") != email_identifier("a@b.com")


class TestCheckRateLimit:
    """Tests for the backward-compatible login check_rate_limit method."""

    @patch("src.modules.identity.infrastructure.rate_limiter.time.time")
    async def test_allows_request_when_under_limit(
        self, mock_time: MagicMock, rate_limiter: RateLimiter, mock_redis: AsyncMock
    ) -> None:
        """First request from an IP should be allowed."""
        mock_time.return_value = 1000.0
        mock_redis.eval.return_value = 1

        result = await rate_limiter.check_rate_limit("192.168.1.1")

        assert result is True

    async def test_blocks_request_at_limit(
        self, rate_limiter: RateLimiter, mock_redis: AsyncMock
    ) -> None:
        """Request should be blocked when count reaches max_requests."""
        mock_redis.eval.return_value = 0

        result = await rate_limiter.check_rate_limit("192.168.1.1")

        assert result is False

    @patch("src.modules.identity.infrastructure.rate_limiter.time.time")
    async def test_uses_correct_key_and_login_limits(
        self, mock_time: MagicMock, rate_limiter: RateLimiter, mock_redis: AsyncMock
    ) -> None:
        """Eval is called once with login key, login limits, and a unique member."""
        mock_time.return_value = 1000.0
        mock_redis.eval.return_value = 1

        await rate_limiter.check_rate_limit("10.0.0.1")

        mock_redis.eval.assert_called_once()
        args = mock_redis.eval.call_args
        script, numkeys, key, now, window, max_requests, member = args[0]
        assert numkeys == 1
        assert key == "rate_limit:login:10.0.0.1"
        assert now == 1000.0
        assert window == rate_limiter._window_seconds
        assert max_requests == rate_limiter._max_requests
        assert member.startswith("1000.0:")

    @patch("src.modules.identity.infrastructure.rate_limiter.time.time")
    @patch("src.modules.identity.infrastructure.rate_limiter.secrets.token_hex")
    async def test_member_is_unique_per_request(
        self, mock_token_hex: MagicMock, mock_time: MagicMock,
        rate_limiter: RateLimiter, mock_redis: AsyncMock,
    ) -> None:
        """Same timestamp still yields a distinct member (no zset collision)."""
        mock_time.return_value = 1000.0
        mock_token_hex.side_effect = ["aaaa", "bbbb"]
        mock_redis.eval.return_value = 1

        await rate_limiter.check_rate_limit("10.0.0.1")
        await rate_limiter.check_rate_limit("10.0.0.1")

        calls = mock_redis.eval.call_args_list
        assert calls[0].args[-1] == "1000.0:aaaa"
        assert calls[1].args[-1] == "1000.0:bbbb"


class TestRateLimiterWithCustomSettings:
    """Tests with non-default rate limit settings."""

    def _limiter(self, mock_redis: AsyncMock, max_requests: int, window: int) -> RateLimiter:
        settings = AuthSettings(
            google_client_id="test-client-id",
            google_client_secret="test-client-secret",
            jwt_secret_key="test-jwt-secret-key-at-least-32-chars-long",
            oauth_token_encryption_key="dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcyE=",
            rate_limit_login_max=max_requests,
            rate_limit_login_window_seconds=window,
        )
        return RateLimiter(mock_redis, settings)

    async def test_custom_limits_reach_eval(self, mock_redis: AsyncMock) -> None:
        """Custom login limits flow into the eval arguments."""
        limiter = self._limiter(mock_redis, max_requests=10, window=120)
        mock_redis.eval.return_value = 1

        await limiter.check_rate_limit("10.0.0.1")

        args = mock_redis.eval.call_args[0]
        assert args[4] == 120  # window
        assert args[5] == 10  # max_requests


class TestCheckRateLimitFor:
    """Tests for the generalized check_rate_limit_for method."""

    async def test_allows_request_under_custom_rule(
        self, rate_limiter: RateLimiter, mock_redis: AsyncMock
    ) -> None:
        """Request should be allowed when count is below the per-call rule."""
        mock_redis.eval.return_value = 1

        result = await rate_limiter.check_rate_limit_for(
            key_prefix="forgot_password:ip",
            identifier="203.0.113.7",
            rule=RateLimitRule(max_requests=3, window_seconds=900),
        )

        assert result is True

    async def test_blocks_request_at_custom_rule(
        self, rate_limiter: RateLimiter, mock_redis: AsyncMock
    ) -> None:
        """Request should be blocked when count reaches the per-call rule."""
        mock_redis.eval.return_value = 0

        result = await rate_limiter.check_rate_limit_for(
            key_prefix="forgot_password:email",
            identifier="abc123hash",
            rule=RateLimitRule(max_requests=2, window_seconds=900),
        )

        assert result is False

    @patch("src.modules.identity.infrastructure.rate_limiter.time.time")
    async def test_uses_custom_key_prefix_identifier_and_rule(
        self, mock_time: MagicMock, rate_limiter: RateLimiter, mock_redis: AsyncMock
    ) -> None:
        """Key format is 'rate_limit:{key_prefix}:{identifier}' and rule values pass through."""
        mock_time.return_value = 1000.0
        mock_redis.eval.return_value = 1

        await rate_limiter.check_rate_limit_for(
            key_prefix="forgot_password:email",
            identifier="abc123hash",
            rule=RateLimitRule(max_requests=2, window_seconds=900),
        )

        mock_redis.eval.assert_called_once()
        script, numkeys, key, now, window, max_requests, member = mock_redis.eval.call_args[0]
        assert numkeys == 1
        assert key == "rate_limit:forgot_password:email:abc123hash"
        assert now == 1000.0
        assert window == 900
        assert max_requests == 2
        assert member.startswith("1000.0:")

    async def test_eval_script_is_atomic_single_call(
        self, rate_limiter: RateLimiter, mock_redis: AsyncMock
    ) -> None:
        """The whole check-and-record runs in one EVAL (no read/write race)."""
        mock_redis.eval.return_value = 1

        await rate_limiter.check_rate_limit_for(
            key_prefix="login",
            identifier="10.1.2.3",
            rule=RateLimitRule(max_requests=5, window_seconds=60),
        )

        assert mock_redis.eval.await_count == 1
        # No pipeline is used at all
        mock_redis.pipeline.assert_not_called()

    async def test_check_rate_limit_remains_backward_compatible(
        self, rate_limiter: RateLimiter, mock_redis: AsyncMock
    ) -> None:
        """check_rate_limit still maps to the login key and login window."""
        mock_redis.eval.return_value = 1

        result = await rate_limiter.check_rate_limit("10.1.2.3")

        assert result is True
        assert mock_redis.eval.call_args[0][2] == "rate_limit:login:10.1.2.3"
        assert mock_redis.eval.call_args[0][4] == rate_limiter._window_seconds

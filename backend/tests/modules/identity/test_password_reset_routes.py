"""Tests for the forgot/reset password API endpoints.

Covers POST /api/auth/forgot-password (dual rate limiting + anti-enumeration),
GET /api/auth/reset-password-token-info, and POST /api/auth/reset-password
using mocked dependencies, following the conventions of test_router.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.identity.api.router import (
    _FORGOT_PASSWORD_GENERIC_MESSAGE,
    _PASSWORD_RESET_SUCCESS_MESSAGE,
    get_rate_limiter,
    get_settings,
    router,
)
from src.modules.identity.container import get_password_reset_service
from src.modules.identity.domain.exceptions import InvalidResetTokenError
from src.modules.identity.infrastructure.config import AuthSettings
from src.modules.identity.infrastructure.rate_limiter import RateLimitRule, email_identifier


@pytest.fixture
def auth_settings() -> AuthSettings:
    """Create AuthSettings with default rate limit values for testing."""
    return AuthSettings(
        google_client_id="test-client-id",
        google_client_secret="test-client-secret",
        google_redirect_uri="http://localhost:8000/api/auth/callback",
        jwt_secret_key="test-jwt-secret-key-at-least-32-chars-long",
        oauth_token_encryption_key="dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcyE=",
        rate_limit_login_max=5,
        rate_limit_login_window_seconds=60,
    )


@pytest.fixture
def mock_password_reset_service() -> AsyncMock:
    """Create a mock PasswordResetService."""
    service = AsyncMock()
    service.create_reset_token = AsyncMock(return_value=True)
    service.validate_token = AsyncMock(return_value=True)
    service.reset_password = AsyncMock()
    return service


@pytest.fixture
def mock_rate_limiter() -> MagicMock:
    """Create a mock RateLimiter that allows every request."""
    limiter = MagicMock()
    limiter.check_rate_limit_for = AsyncMock(return_value=True)
    return limiter


@pytest.fixture
def app(
    mock_password_reset_service: AsyncMock,
    mock_rate_limiter: MagicMock,
    auth_settings: AuthSettings,
) -> FastAPI:
    """Build an app with the auth router and mocked dependencies."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    from src.modules.identity.domain.exceptions import AuthError

    app = FastAPI()

    @app.exception_handler(AuthError)
    async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.error_code, "message": exc.message}},
        )

    app.include_router(router)

    app.dependency_overrides[get_password_reset_service] = lambda: mock_password_reset_service
    app.dependency_overrides[get_rate_limiter] = lambda: mock_rate_limiter
    app.dependency_overrides[get_settings] = lambda: auth_settings

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, follow_redirects=False)


class TestForgotPasswordEndpoint:
    """Tests for POST /api/auth/forgot-password."""

    def test_returns_200_generic_message_for_existing_email(
        self, client: TestClient, mock_password_reset_service: AsyncMock
    ) -> None:
        """A registered email gets the generic 200 message."""
        mock_password_reset_service.create_reset_token = AsyncMock(return_value=True)

        response = client.post("/api/auth/forgot-password", json={"email": "hr@example.com"})

        assert response.status_code == 200
        assert response.json() == {"message": _FORGOT_PASSWORD_GENERIC_MESSAGE}

    def test_returns_200_generic_message_for_unknown_email(
        self, client: TestClient, mock_password_reset_service: AsyncMock
    ) -> None:
        """An unregistered email still gets the identical generic 200 message."""
        mock_password_reset_service.create_reset_token = AsyncMock(return_value=False)

        response = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})

        assert response.status_code == 200
        assert response.json() == {"message": _FORGOT_PASSWORD_GENERIC_MESSAGE}

    def test_returns_200_generic_message_when_email_send_fails(
        self, client: TestClient, mock_password_reset_service: AsyncMock
    ) -> None:
        """A failed email send must not be distinguishable from success."""
        mock_password_reset_service.create_reset_token = AsyncMock(return_value=False)

        response = client.post("/api/auth/forgot-password", json={"email": "hr@example.com"})

        assert response.status_code == 200
        assert response.json() == {"message": _FORGOT_PASSWORD_GENERIC_MESSAGE}

    def test_returns_422_for_invalid_email(self, client: TestClient) -> None:
        """Malformed emails are rejected by schema validation."""
        response = client.post("/api/auth/forgot-password", json={"email": "not-an-email"})

        assert response.status_code == 422

    def test_calls_create_reset_token_with_email_and_client_ip(
        self, client: TestClient, mock_password_reset_service: AsyncMock
    ) -> None:
        """The service is invoked with the normalized email and client IP."""
        client.post("/api/auth/forgot-password", json={"email": "hr@example.com"})

        mock_password_reset_service.create_reset_token.assert_awaited_once_with(
            "hr@example.com", "testclient"
        )

    def test_returns_429_when_ip_rate_limit_exceeded(
        self,
        client: TestClient,
        mock_rate_limiter: MagicMock,
        mock_password_reset_service: AsyncMock,
    ) -> None:
        """Exceeding the per-IP limit returns 429 before any work happens."""
        mock_rate_limiter.check_rate_limit_for = AsyncMock(side_effect=[False, True])

        response = client.post("/api/auth/forgot-password", json={"email": "hr@example.com"})

        assert response.status_code == 429
        mock_password_reset_service.create_reset_token.assert_not_awaited()

    def test_returns_429_when_email_rate_limit_exceeded(
        self,
        client: TestClient,
        mock_rate_limiter: MagicMock,
        mock_password_reset_service: AsyncMock,
    ) -> None:
        """Exceeding the per-email limit returns 429 before any work happens."""
        mock_rate_limiter.check_rate_limit_for = AsyncMock(side_effect=[True, False])

        response = client.post("/api/auth/forgot-password", json={"email": "hr@example.com"})

        assert response.status_code == 429
        mock_password_reset_service.create_reset_token.assert_not_awaited()

    def test_uses_settings_limits_and_hashed_email_identifier(
        self,
        client: TestClient,
        mock_rate_limiter: MagicMock,
        auth_settings: AuthSettings,
    ) -> None:
        """The dual checks use the configured limits, IP identifier, and hashed email."""
        client.post("/api/auth/forgot-password", json={"email": "hr@example.com"})

        calls = mock_rate_limiter.check_rate_limit_for.await_args_list
        assert len(calls) == 2

        ip_call, email_call = calls
        assert ip_call.kwargs == {
            "key_prefix": "forgot_password:ip",
            "identifier": "testclient",
            "rule": RateLimitRule(
                max_requests=auth_settings.rate_limit_forgot_password_ip_max,
                window_seconds=auth_settings.rate_limit_forgot_password_ip_window_seconds,
            ),
        }
        assert email_call.kwargs == {
            "key_prefix": "forgot_password:email",
            "identifier": email_identifier("hr@example.com"),
            "rule": RateLimitRule(
                max_requests=auth_settings.rate_limit_forgot_password_email_max,
                window_seconds=auth_settings.rate_limit_forgot_password_email_window_seconds,
            ),
        }


class TestResetPasswordTokenInfoEndpoint:
    """Tests for GET /api/auth/reset-password-token-info."""

    def test_returns_true_for_valid_token(
        self, client: TestClient, mock_password_reset_service: AsyncMock
    ) -> None:
        """A usable token reports valid=True."""
        mock_password_reset_service.validate_token = AsyncMock(return_value=True)

        response = client.get(
            "/api/auth/reset-password-token-info", params={"token": "abc123"}
        )

        assert response.status_code == 200
        assert response.json() == {"valid": True}
        mock_password_reset_service.validate_token.assert_awaited_once_with("abc123")

    def test_returns_false_for_invalid_token(
        self, client: TestClient, mock_password_reset_service: AsyncMock
    ) -> None:
        """An unknown/used/expired token reports valid=False."""
        mock_password_reset_service.validate_token = AsyncMock(return_value=False)

        response = client.get(
            "/api/auth/reset-password-token-info", params={"token": "bogus"}
        )

        assert response.status_code == 200
        assert response.json() == {"valid": False}


class TestResetPasswordEndpoint:
    """Tests for POST /api/auth/reset-password."""

    def test_returns_200_success_message(
        self, client: TestClient, mock_password_reset_service: AsyncMock
    ) -> None:
        """A successful reset returns the success message."""
        response = client.post(
            "/api/auth/reset-password",
            json={"token": "abc123", "new_password": "NewPassword123!"},
        )

        assert response.status_code == 200
        assert response.json() == {"message": _PASSWORD_RESET_SUCCESS_MESSAGE}
        mock_password_reset_service.reset_password.assert_awaited_once_with(
            "abc123", "NewPassword123!"
        )

    def test_returns_400_when_token_invalid(
        self, client: TestClient, mock_password_reset_service: AsyncMock
    ) -> None:
        """InvalidResetTokenError propagates as HTTP 400."""
        mock_password_reset_service.reset_password = AsyncMock(
            side_effect=InvalidResetTokenError()
        )

        response = client.post(
            "/api/auth/reset-password",
            json={"token": "expired", "new_password": "NewPassword123!"},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "AUTH_INVALID_RESET_TOKEN"

    def test_returns_422_when_password_too_short(self, client: TestClient) -> None:
        """Passwords shorter than 12 chars are rejected by schema validation."""
        response = client.post(
            "/api/auth/reset-password",
            json={"token": "abc123", "new_password": "short"},
        )

        assert response.status_code == 422

    def test_returns_422_when_password_too_long(self, client: TestClient) -> None:
        """Passwords longer than 255 chars are rejected by schema validation."""
        response = client.post(
            "/api/auth/reset-password",
            json={"token": "abc123", "new_password": "x" * 256},
        )

        assert response.status_code == 422

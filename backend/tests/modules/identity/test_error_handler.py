"""Tests for the Identity & Auth error handler."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.identity.api.error_handler import register_auth_error_handlers
from src.modules.identity.domain.exceptions import (
    AccessDeniedError,
    AuthError,
    GoogleAuthError,
    InsufficientScopeError,
    InvalidStateError,
    InvalidTokenError,
    RateLimitExceededError,
)


@pytest.fixture()
def app() -> FastAPI:
    """Create a FastAPI app with auth error handlers registered."""
    app = FastAPI()
    register_auth_error_handlers(app)
    return app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    """Create a test client for the app."""
    return TestClient(app)


class TestAuthErrorHandler:
    """Tests for the AuthError exception handler."""

    def test_handles_base_auth_error(self, app: FastAPI, client: TestClient) -> None:
        """AuthError base class returns 500 with correct JSON structure."""

        @app.get("/test")
        async def _raise_auth_error() -> None:
            raise AuthError()

        response = client.get("/test")
        assert response.status_code == 500
        assert response.json() == {
            "error": {
                "code": "AUTH_ERROR",
                "message": "Lỗi xác thực hệ thống",
            }
        }

    def test_handles_invalid_state_error(self, app: FastAPI, client: TestClient) -> None:
        """InvalidStateError returns 400 with AUTH_INVALID_STATE code."""

        @app.get("/test-state")
        async def _raise_invalid_state() -> None:
            raise InvalidStateError()

        response = client.get("/test-state")
        assert response.status_code == 400
        assert response.json() == {
            "error": {
                "code": "AUTH_INVALID_STATE",
                "message": "Trạng thái xác thực không hợp lệ",
            }
        }

    def test_handles_google_auth_error(self, app: FastAPI, client: TestClient) -> None:
        """GoogleAuthError returns 502 with AUTH_GOOGLE_ERROR code."""

        @app.get("/test-google")
        async def _raise_google_error() -> None:
            raise GoogleAuthError()

        response = client.get("/test-google")
        assert response.status_code == 502
        assert response.json() == {
            "error": {
                "code": "AUTH_GOOGLE_ERROR",
                "message": "Xác thực Google thất bại",
            }
        }

    def test_handles_access_denied_error(self, app: FastAPI, client: TestClient) -> None:
        """AccessDeniedError returns 403 with AUTH_ACCESS_DENIED code."""

        @app.get("/test-denied")
        async def _raise_access_denied() -> None:
            raise AccessDeniedError()

        response = client.get("/test-denied")
        assert response.status_code == 403
        assert response.json() == {
            "error": {
                "code": "AUTH_ACCESS_DENIED",
                "message": "Truy cập bị từ chối. Vui lòng liên hệ quản trị viên.",
            }
        }

    def test_handles_insufficient_scope_error(self, app: FastAPI, client: TestClient) -> None:
        """InsufficientScopeError returns 400 with AUTH_INSUFFICIENT_SCOPE code."""

        @app.get("/test-scope")
        async def _raise_insufficient_scope() -> None:
            raise InsufficientScopeError()

        response = client.get("/test-scope")
        assert response.status_code == 400
        assert response.json() == {
            "error": {
                "code": "AUTH_INSUFFICIENT_SCOPE",
                "message": "Vui lòng cấp tất cả quyền được yêu cầu",
            }
        }

    def test_handles_invalid_token_error(self, app: FastAPI, client: TestClient) -> None:
        """InvalidTokenError returns 401 with AUTH_INVALID_TOKEN code."""

        @app.get("/test-token")
        async def _raise_invalid_token() -> None:
            raise InvalidTokenError()

        response = client.get("/test-token")
        assert response.status_code == 401
        assert response.json() == {
            "error": {
                "code": "AUTH_INVALID_TOKEN",
                "message": "Phiên đăng nhập không hợp lệ hoặc đã hết hạn",
            }
        }

    def test_handles_rate_limit_exceeded_error(self, app: FastAPI, client: TestClient) -> None:
        """RateLimitExceededError returns 429 with AUTH_RATE_LIMITED code."""

        @app.get("/test-rate")
        async def _raise_rate_limit() -> None:
            raise RateLimitExceededError()

        response = client.get("/test-rate")
        assert response.status_code == 429
        assert response.json() == {
            "error": {
                "code": "AUTH_RATE_LIMITED",
                "message": "Quá nhiều lần đăng nhập. Vui lòng thử lại sau.",
            }
        }

    def test_free_form_message_is_withheld_from_the_response(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """A free-form message is log-only: adapters build it from ``str(exc)``.

        Only ``public_context`` values reach the body, so the response keeps
        the catalog entry for the code instead of whatever the raise site
        happened to interpolate.
        """

        @app.get("/test-custom")
        async def _raise_custom_message() -> None:
            raise InvalidStateError("State token expired after 10 minutes")

        response = client.get("/test-custom")
        assert response.status_code == 400
        assert response.json() == {
            "error": {
                "code": "AUTH_INVALID_STATE",
                "message": "Trạng thái xác thực không hợp lệ",
            }
        }

    def test_response_content_type_is_json(self, app: FastAPI, client: TestClient) -> None:
        """Error responses have application/json content type."""

        @app.get("/test-content-type")
        async def _raise_error() -> None:
            raise AuthError()

        response = client.get("/test-content-type")
        assert response.headers["content-type"] == "application/json"

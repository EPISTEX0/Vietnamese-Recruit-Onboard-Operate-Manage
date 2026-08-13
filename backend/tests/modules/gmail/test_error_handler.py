"""Tests for the Gmail API error handler."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.modules.gmail.api.error_handler import register_gmail_error_handlers
from src.modules.gmail.domain.exceptions import (
    GmailConnectFailedException,
    GmailError,
    GmailFetchError,
    GmailNotConnectedException,
    GmailSendFailedException,
    MessageNotFoundException,
    RateLimitedException,
    UnauthorizedException,
)


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with Gmail error handlers registered."""
    app = FastAPI()
    register_gmail_error_handlers(app)
    return app


@pytest.fixture
def app_with_routes(app: FastAPI) -> FastAPI:
    """Add test routes that raise each exception type."""

    @app.get("/test/unauthorized")
    async def raise_unauthorized():
        raise UnauthorizedException()

    @app.get("/test/connect-failed")
    async def raise_connect_failed():
        raise GmailConnectFailedException()

    @app.get("/test/not-connected")
    async def raise_not_connected():
        raise GmailNotConnectedException()

    @app.get("/test/fetch-error")
    async def raise_fetch_error():
        raise GmailFetchError()

    @app.get("/test/message-not-found")
    async def raise_message_not_found():
        raise MessageNotFoundException()

    @app.get("/test/send-failed")
    async def raise_send_failed():
        raise GmailSendFailedException()

    @app.get("/test/rate-limited")
    async def raise_rate_limited():
        raise RateLimitedException(retry_after=25)

    @app.get("/test/rate-limited-zero")
    async def raise_rate_limited_zero():
        raise RateLimitedException(retry_after=0)

    @app.get("/test/base-error")
    async def raise_base_error():
        raise GmailError("Something went wrong")

    @app.get("/test/custom-message")
    async def raise_custom_message():
        raise GmailFetchError("Custom fetch error message")

    return app


@pytest.fixture
async def client(app_with_routes: FastAPI) -> AsyncClient:
    """Create an async test client."""
    transport = ASGITransport(app=app_with_routes)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestUnauthorizedException:
    @pytest.mark.anyio
    async def test_returns_401(self, client: AsyncClient):
        response = await client.get("/test/unauthorized")
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/unauthorized")
        data = response.json()
        assert data["error_code"] == "UNAUTHORIZED"
        # Vietnamese is the default response language for this HRM.
        assert data["message"] == "Phiên đăng nhập không hợp lệ hoặc đã hết hạn"

    @pytest.mark.anyio
    async def test_accept_language_en_returns_english_message(self, client: AsyncClient):
        response = await client.get("/test/unauthorized", headers={"Accept-Language": "en"})
        data = response.json()
        assert data["error_code"] == "UNAUTHORIZED"
        assert data["message"] == "Session is invalid or has expired"


class TestGmailConnectFailedException:
    @pytest.mark.anyio
    async def test_returns_400(self, client: AsyncClient):
        response = await client.get("/test/connect-failed")
        assert response.status_code == 400

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/connect-failed")
        data = response.json()
        assert data["error_code"] == "GMAIL_CONNECT_FAILED"


class TestGmailNotConnectedException:
    @pytest.mark.anyio
    async def test_returns_403(self, client: AsyncClient):
        response = await client.get("/test/not-connected")
        assert response.status_code == 403

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/not-connected")
        data = response.json()
        assert data["error_code"] == "GMAIL_NOT_CONNECTED"


class TestGmailFetchError:
    @pytest.mark.anyio
    async def test_returns_502(self, client: AsyncClient):
        response = await client.get("/test/fetch-error")
        assert response.status_code == 502

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/fetch-error")
        data = response.json()
        assert data["error_code"] == "GMAIL_FETCH_ERROR"


class TestMessageNotFoundException:
    @pytest.mark.anyio
    async def test_returns_404(self, client: AsyncClient):
        response = await client.get("/test/message-not-found")
        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/message-not-found")
        data = response.json()
        assert data["error_code"] == "MESSAGE_NOT_FOUND"


class TestGmailSendFailedException:
    @pytest.mark.anyio
    async def test_returns_502(self, client: AsyncClient):
        response = await client.get("/test/send-failed")
        assert response.status_code == 502

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/send-failed")
        data = response.json()
        assert data["error_code"] == "GMAIL_SEND_FAILED"


class TestRateLimitedException:
    @pytest.mark.anyio
    async def test_returns_429(self, client: AsyncClient):
        response = await client.get("/test/rate-limited")
        assert response.status_code == 429

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/rate-limited")
        data = response.json()
        assert data["error_code"] == "RATE_LIMITED"

    @pytest.mark.anyio
    async def test_includes_retry_after_header(self, client: AsyncClient):
        response = await client.get("/test/rate-limited")
        assert response.headers["retry-after"] == "25"

    @pytest.mark.anyio
    async def test_no_retry_after_header_when_zero(self, client: AsyncClient):
        response = await client.get("/test/rate-limited-zero")
        assert "retry-after" not in response.headers


class TestBaseGmailError:
    @pytest.mark.anyio
    async def test_returns_500(self, client: AsyncClient):
        response = await client.get("/test/base-error")
        assert response.status_code == 500

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/base-error")
        data = response.json()
        assert data["error_code"] == "GMAIL_ERROR"
        assert data["message"] == "Something went wrong"


class TestCustomMessage:
    @pytest.mark.anyio
    async def test_custom_message_in_response(self, client: AsyncClient):
        response = await client.get("/test/custom-message")
        data = response.json()
        assert data["message"] == "Custom fetch error message"


class TestResponseFormat:
    @pytest.mark.anyio
    async def test_response_matches_error_schema(self, client: AsyncClient):
        """Verify response body matches ErrorResponse schema structure."""
        response = await client.get("/test/fetch-error")
        data = response.json()
        assert "error_code" in data
        assert "message" in data
        assert "details" in data
        assert data["details"] is None

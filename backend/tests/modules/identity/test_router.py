"""Unit tests for the Identity & Auth router endpoints.

Tests the FastAPI router endpoints for setup, local login, refresh,
logout, me, and grant-status using mocked dependencies.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.identity.api.router import (
    get_auth_service,
    get_current_user,
    get_oauth_service,
    get_rate_limiter,
    get_session,
    get_token_service,
    router,
)
from src.modules.identity.api.schemas import GrantStatus
from src.modules.identity.domain.exceptions import InvalidTokenError


@pytest.fixture
def mock_auth_service():
    service = AsyncMock()
    service.logout = AsyncMock()
    service.get_setup_status = AsyncMock(return_value=False)
    service.setup_first_run = AsyncMock()
    return service


@pytest.fixture
def mock_token_service():
    service = AsyncMock()
    service.refresh_access_token = AsyncMock(return_value="refreshed-access-token")
    return service


@pytest.fixture
def mock_oauth_service():
    service = AsyncMock()
    grant = MagicMock()
    grant.is_valid = True
    grant.scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/calendar.events",
    ]
    service._grant_repository = AsyncMock()
    service._grant_repository.get_by_user_id = AsyncMock(return_value=grant)
    service.determine_grant_status = MagicMock(
        return_value=GrantStatus(gmail_grant_valid=True, calendar_grant_valid=True)
    )
    return service


@pytest.fixture
def mock_current_user():
    user = MagicMock()
    user.id = uuid4()
    user.email = "hr@example.com"
    user.name = "HR User"
    user.avatar_url = "https://example.com/avatar.png"
    user.employee_id = None
    user.role = "user"
    user.must_change_password = False
    user.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    user.last_login = datetime(2024, 6, 15, tzinfo=UTC)
    return user


@pytest.fixture
def app(
    mock_auth_service,
    mock_token_service,
    mock_oauth_service,
    mock_current_user,
):
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

    app.dependency_overrides[get_auth_service] = lambda: mock_auth_service
    app.dependency_overrides[get_token_service] = lambda: mock_token_service
    app.dependency_overrides[get_oauth_service] = lambda: mock_oauth_service
    app.dependency_overrides[get_current_user] = lambda: mock_current_user
    rate_limiter = MagicMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=True)
    app.dependency_overrides[get_rate_limiter] = lambda: rate_limiter
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    # Writing endpoints commit before returning (#320), so the double has to
    # honour that await the same way it already does for ``execute``. This
    # stands in for a session; it asserts nothing about the commit -- that is
    # test_router_commit.py's job, which is why it calls handlers directly
    # instead of overriding this dependency away.
    session.commit = AsyncMock()
    app.dependency_overrides[get_session] = lambda: session

    return app


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


class TestSetupEndpoint:
    def test_setup_status_is_minimal(self, client, mock_auth_service):
        response = client.get("/api/auth/setup-status")
        assert response.status_code == 200
        assert response.json() == {"setup_complete": False}
        mock_auth_service.get_setup_status.assert_awaited_once_with()

    def test_setup_creates_session(self, client, mock_auth_service):
        user = SimpleNamespace(
            id=uuid4(),
            email="hr@example.com",
            name="HR Admin",
            avatar_url=None,
            employee_id=None,
            role="hr",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            last_login=datetime(2024, 1, 1, tzinfo=UTC),
        )
        mock_auth_service.setup_first_run.return_value = SimpleNamespace(
            user=user,
            access_token="access",
            refresh_token="refresh",
            must_change_password=False,
        )

        response = client.post(
            "/api/auth/setup",
            json={
                "organization_name": "Acme Vietnam",
                "name": "HR Admin",
                "email": "HR@Example.COM",
                "password": "a" * 12,
                "password_confirmation": "a" * 12,
            },
        )

        assert response.status_code == 200
        assert response.json()["user"]["email"] == "hr@example.com"
        assert "access_token" in response.cookies
        mock_auth_service.setup_first_run.assert_awaited_once_with(
            "Acme Vietnam", "HR Admin", "hr@example.com", "a" * 12
        )


class TestRefreshEndpoint:
    def test_returns_200_with_message(self, client):
        client.cookies.set("refresh_token", "valid-refresh-token")
        response = client.post("/api/auth/refresh")
        assert response.status_code == 200
        assert response.json() == {"message": "Token refreshed"}

    def test_sets_new_access_token_cookie(self, client):
        client.cookies.set("refresh_token", "valid-refresh-token")
        response = client.post("/api/auth/refresh")
        assert "access_token" in response.cookies

    def test_raises_401_when_refresh_token_missing(self, client):
        response = client.post("/api/auth/refresh")
        assert response.status_code == 401

    def test_raises_401_when_token_service_rejects(self, app, mock_token_service):
        mock_token_service.refresh_access_token = AsyncMock(side_effect=InvalidTokenError())
        client = TestClient(app)
        client.cookies.set("refresh_token", "expired-token")
        response = client.post("/api/auth/refresh")
        assert response.status_code == 401


class TestLogoutEndpoint:
    def test_returns_200_with_message(self, client):
        client.cookies.set("refresh_token", "valid-refresh-token")
        response = client.post("/api/auth/logout")
        assert response.status_code == 200
        assert response.json() == {"message": "Logged out"}

    def test_calls_auth_service_logout(self, client, mock_auth_service):
        client.cookies.set("refresh_token", "my-refresh-token")
        client.post("/api/auth/logout")
        mock_auth_service.logout.assert_called_once_with("my-refresh-token")

    def test_clears_access_token_cookie(self, client):
        client.cookies.set("refresh_token", "valid-refresh-token")
        client.cookies.set("access_token", "some-access-token")
        response = client.post("/api/auth/logout")
        set_cookie_headers = response.headers.get_list("set-cookie")
        assert any('access_token=""' in h or "access_token=;" in h for h in set_cookie_headers)

    def test_clears_refresh_token_cookie(self, client):
        client.cookies.set("refresh_token", "valid-refresh-token")
        response = client.post("/api/auth/logout")
        set_cookie_headers = response.headers.get_list("set-cookie")
        assert any('refresh_token=""' in h or "refresh_token=;" in h for h in set_cookie_headers)

    def test_clears_must_change_password_cookie(self, client):
        client.cookies.set("must_change_password", "true")
        response = client.post("/api/auth/logout")
        set_cookie_headers = response.headers.get_list("set-cookie")
        assert any(
            'must_change_password=""' in h or "must_change_password=;" in h
            for h in set_cookie_headers
        )


class TestMeEndpoint:
    def test_returns_200_with_user_data(self, client, mock_current_user):
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        assert response.json()["email"] == mock_current_user.email

    def test_returns_user_id(self, client, mock_current_user):
        response = client.get("/api/auth/me")
        assert response.json()["id"] == str(mock_current_user.id)

    def test_returns_avatar_url(self, client, mock_current_user):
        response = client.get("/api/auth/me")
        assert response.json()["avatar_url"] == mock_current_user.avatar_url


class TestGrantStatusEndpoint:
    def test_returns_200_with_grant_status(self, client):
        response = client.get("/api/auth/grant-status")
        assert response.status_code == 200
        data = response.json()
        assert "gmail_grant_valid" in data
        assert "calendar_grant_valid" in data

    def test_returns_valid_grants(self, client):
        response = client.get("/api/auth/grant-status")
        data = response.json()
        assert data["gmail_grant_valid"] is True
        assert data["calendar_grant_valid"] is True

    def test_returns_invalid_grants_when_no_grant(self, app, mock_oauth_service):
        mock_oauth_service._grant_repository.get_by_user_id = AsyncMock(return_value=None)
        client = TestClient(app)
        response = client.get("/api/auth/grant-status")
        data = response.json()
        assert data["gmail_grant_valid"] is False
        assert data["calendar_grant_valid"] is False

    def test_succeeds_without_refresh_token(self, client, mock_auth_service):
        response = client.post("/api/auth/logout")
        assert response.status_code == 200
        mock_auth_service.logout.assert_not_called()

        response = client.post("/api/auth/logout")
        assert response.status_code == 200
        mock_auth_service.logout.assert_not_called()


class TestAdminSchemasCompat:
    def test_identity_api_schemas_exports_admin_dtos(self):
        from src.modules.identity.api.schemas import (
            OAuthConfigResponse,
            OAuthConfigUpdateRequest,
        )

        assert OAuthConfigResponse is not None
        assert OAuthConfigUpdateRequest is not None

    def test_google_connection_routes_exist(self, client):
        assert client.get("/api/auth/organization-google-connection/authorize-url").status_code in {
            200,
            401,
            403,
        }
        assert client.post("/api/auth/organization-google-connection/reconnect").status_code in {
            200,
            401,
            403,
        }

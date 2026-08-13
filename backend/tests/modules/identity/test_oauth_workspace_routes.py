from __future__ import annotations

import base64
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.identity.api.router import router
from src.modules.identity.api.schemas import GoogleWorkspaceConnectionResponse
from src.modules.identity.application.audit_service import AuditService
from src.modules.identity.application.organization_google_connection_service import (
    GOOGLE_AUTH_URL,
    GOOGLE_REVOKE_URL,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    REQUIRED_SCOPES,
    OrganizationGoogleConnectionResponse,
    OrganizationGoogleConnectionService,
)
from src.modules.identity.container import get_current_user
from src.modules.identity.domain.entities import User
from src.modules.identity.domain.exceptions import DomainAccessDeniedError, InvalidStateError
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils
from src.modules.identity.infrastructure.jwt_utils import JWTUtils


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, object]:
        return self._payload


class FakeHttpClient:
    def __init__(
        self,
        *,
        token: FakeResponse | Exception,
        userinfo: FakeResponse | Exception,
        revoke: FakeResponse | Exception,
    ) -> None:
        self.token = token
        self.userinfo = userinfo
        self.revoke = revoke
        self.posts: list[tuple[str, dict[str, object] | None]] = []
        self.gets: list[tuple[str, dict[str, object] | None]] = []

    async def post(
        self,
        url: str,
        data: dict[str, object] | None = None,
        headers: dict[str, object] | None = None,
    ):
        self.posts.append((url, data))
        if url == GOOGLE_TOKEN_URL:
            if isinstance(self.token, Exception):
                raise self.token
            return self.token
        if url == GOOGLE_REVOKE_URL:
            if isinstance(self.revoke, Exception):
                raise self.revoke
            return self.revoke
        raise AssertionError(url)

    async def get(self, url: str, headers: dict[str, object] | None = None):
        self.gets.append((url, headers))
        if url == GOOGLE_USERINFO_URL:
            if isinstance(self.userinfo, Exception):
                raise self.userinfo
            return self.userinfo
        raise AssertionError(url)


@pytest.fixture
def crypto() -> CryptoUtils:
    return CryptoUtils(base64.b64encode(b"0" * 32).decode("ascii"))


@pytest.fixture
def state_jwt() -> JWTUtils:
    return JWTUtils("state-secret")


@pytest.fixture
def hr_user() -> User:
    return User(
        id=uuid4(),
        email="hr@example.com",
        name="HR",
        avatar_url=None,
        password_hash="x",
        role="hr",
        must_change_password=False,
        created_at=datetime.now(UTC),
        last_login=datetime.now(UTC),
    )


class DurableConnectionRepo:
    def __init__(self) -> None:
        self.state = None
        self.upsert_calls = 0
        self.disconnect_calls = 0

    async def get_singleton(self):
        return self.state

    async def upsert_singleton(self, connection):
        self.state = connection
        self.upsert_calls += 1
        return connection

    async def disconnect(self):
        self.disconnect_calls += 1
        self.state = None
        return None


@pytest.fixture
def oauth_config_manager() -> AsyncMock:
    manager = AsyncMock()
    manager.get_effective_credentials = AsyncMock(
        return_value=SimpleNamespace(
            client_id="cid",
            client_secret="secret",
            redirect_uri="http://test/callback",
        )
    )
    return manager


@pytest.fixture
def oauth_grant_repo() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def org_settings_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_allowed_domains = AsyncMock(return_value=["example.com"])
    return repo


@pytest.fixture
def audit_service() -> AsyncMock:
    svc = AsyncMock(spec=AuditService)
    svc.log_action = AsyncMock()
    return svc


@pytest.fixture
def http_client() -> FakeHttpClient:
    return FakeHttpClient(
        token=FakeResponse(
            200,
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "scope": " ".join(REQUIRED_SCOPES),
                "expires_in": 3600,
            },
        ),
        userinfo=FakeResponse(
            200, {"email": "hr@example.com", "hd": "example.com", "sub": "sub-123"}
        ),
        revoke=FakeResponse(200, {}),
    )


@pytest.fixture
def connection_repo() -> DurableConnectionRepo:
    return DurableConnectionRepo()


@pytest.fixture
def sync_cursor_repo() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(
    connection_repo,
    oauth_config_manager,
    oauth_grant_repo,
    sync_cursor_repo,
    audit_service,
    crypto,
    state_jwt,
    org_settings_repo,
    http_client,
) -> OrganizationGoogleConnectionService:
    return OrganizationGoogleConnectionService(
        connection_repo=connection_repo,
        oauth_config_manager=oauth_config_manager,
        oauth_grant_repo=oauth_grant_repo,
        sync_cursor_repo=sync_cursor_repo,
        audit_service=audit_service,
        crypto=crypto,
        state_jwt=state_jwt,
        org_settings_repo=org_settings_repo,
        http_client=http_client,
    )


@pytest.fixture
def app(hr_user: User) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: hr_user
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_schema_accepts_basic_payload() -> None:
    assert GoogleWorkspaceConnectionResponse(status="disconnected").status == "disconnected"


@pytest.mark.asyncio
async def test_initiate_builds_offline_consent_url(
    service: OrganizationGoogleConnectionService, hr_user: User
) -> None:
    result = await service.initiate(hr_user)
    assert result.status == "disconnected"
    assert result.redirect_url and result.redirect_url.startswith(GOOGLE_AUTH_URL)
    params = parse_qs(urlparse(result.redirect_url).query)
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["scope"] == [" ".join(REQUIRED_SCOPES)]


@pytest.mark.asyncio
async def test_callback_persists_grant_and_reuses_refresh_token(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    connection_repo: DurableConnectionRepo,
    audit_service: AsyncMock,
) -> None:
    init = await service.initiate(hr_user)
    state = parse_qs(urlparse(init.redirect_url or "").query)["state"][0]

    result = await service.callback(hr=hr_user, state=state, code="code")

    assert result == OrganizationGoogleConnectionResponse(
        status="connected", email="hr@example.com", has_secret=True
    )
    assert connection_repo.upsert_calls >= 2
    stored = connection_repo.state
    assert stored is not None
    assert stored.oauth_state_hash is None
    assert stored.oauth_state_nonce is None
    assert stored.refresh_token_enc
    assert stored.access_token_enc
    assert stored.connected_by_user_id == hr_user.id
    assert audit_service.log_action.await_count == 1


@pytest.mark.asyncio
async def test_callback_accepts_google_canonical_email_scope(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    http_client: FakeHttpClient,
) -> None:
    assert isinstance(http_client.token, FakeResponse)
    http_client.token._payload["scope"] = (
        " ".join(scope for scope in REQUIRED_SCOPES if scope != "email")
        + " https://www.googleapis.com/auth/userinfo.email"
    )
    init = await service.initiate(hr_user)
    state = parse_qs(urlparse(init.redirect_url or "").query)["state"][0]

    result = await service.callback(hr=hr_user, state=state, code="code")

    assert result.status == "connected"


@pytest.mark.asyncio
async def test_callback_rejects_replay_state(
    service: OrganizationGoogleConnectionService, hr_user: User
) -> None:
    init = await service.initiate(hr_user)
    state = parse_qs(urlparse(init.redirect_url or "").query)["state"][0]
    await service.callback(hr=hr_user, state=state, code="code")
    with pytest.raises(InvalidStateError):
        await service.callback(hr=hr_user, state=state, code="code")


@pytest.mark.asyncio
async def test_callback_rejects_wrong_org_domain(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    org_settings_repo: AsyncMock,
) -> None:
    org_settings_repo.get_allowed_domains = AsyncMock(return_value=["other.com"])
    init = await service.initiate(hr_user)
    state = parse_qs(urlparse(init.redirect_url or "").query)["state"][0]
    with pytest.raises(DomainAccessDeniedError):
        await service.callback(hr=hr_user, state=state, code="code")


@pytest.mark.asyncio
async def test_disconnect_revoke_best_effort(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    http_client: FakeHttpClient,
    crypto: CryptoUtils,
) -> None:
    service._connection_repo.get_singleton = AsyncMock(
        return_value=SimpleNamespace(refresh_token_enc=crypto.encrypt("refresh"))
    )
    await service.disconnect(hr_user)
    assert any(url == GOOGLE_REVOKE_URL for url, _ in http_client.posts)
    service._sync_cursor_repo.clear_cursor.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_callback_logs_switch_account_when_email_changes(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    connection_repo: DurableConnectionRepo,
    audit_service: AsyncMock,
    org_settings_repo: AsyncMock,
    http_client: FakeHttpClient,
) -> None:
    await service.initiate(hr_user)
    connection_repo.state.email = "old@example.com"
    init_res = await service.initiate(hr_user)
    state = parse_qs(urlparse(init_res.redirect_url or "").query)["state"][0]
    http_client.userinfo = FakeResponse(
        200, {"email": "new@example.com", "hd": "example.com", "sub": "sub-123"}
    )
    await service.callback(hr=hr_user, state=state, code="code")
    assert (
        audit_service.log_action.await_args.kwargs["action_type"].value
        == "org_google_switch_account"
    )


@pytest.mark.asyncio
async def test_callback_allows_any_verified_email_when_allowed_domains_empty(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    org_settings_repo: AsyncMock,
    http_client: FakeHttpClient,
) -> None:
    org_settings_repo.get_allowed_domains = AsyncMock(return_value=[])
    http_client.userinfo = FakeResponse(200, {"email": "personal@gmail.com", "sub": "gmail-sub"})
    init = await service.initiate(hr_user)
    state = parse_qs(urlparse(init.redirect_url or "").query)["state"][0]

    result = await service.callback(hr=hr_user, state=state, code="code")

    assert result.status == "connected"


@pytest.mark.asyncio
async def test_connection_status_exposes_degraded_state(
    service: OrganizationGoogleConnectionService,
    connection_repo: DurableConnectionRepo,
) -> None:
    connection_repo.state = SimpleNamespace(
        status="degraded",
        email="shared@example.com",
        google_sub="sub",
        client_secret_enc="encrypted-secret",
        selected_calendar_id="test-calendar-id",
    )

    result = await service.get_status()

    assert result.status == "degraded"
    assert result.email == "shared@example.com"
    assert result.has_secret is True
    assert result.selected_calendar_id == "test-calendar-id"


@pytest.mark.asyncio
async def test_callback_rejects_when_redirect_uri_changes(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    oauth_config_manager: AsyncMock,
) -> None:
    init = await service.initiate(hr_user)
    state = parse_qs(urlparse(init.redirect_url or "").query)["state"][0]
    oauth_config_manager.get_effective_credentials.return_value.redirect_uri = (
        "http://test/changed-callback"
    )

    with pytest.raises(InvalidStateError):
        await service.callback(hr=hr_user, state=state, code="code")


@pytest.mark.asyncio
async def test_reconnect_preserves_refresh_token_when_google_omits_it(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    connection_repo: DurableConnectionRepo,
    http_client: FakeHttpClient,
    crypto: CryptoUtils,
) -> None:
    connection_repo.state = SimpleNamespace(
        status="connected",
        email="hr@example.com",
        google_sub="old-sub",
        email_domain="example.com",
        selected_calendar_id="calendar",
        credential_format_version=1,
        credential_key_version=1,
        access_token_enc=crypto.encrypt("old-access"),
        refresh_token_enc=crypto.encrypt("existing-refresh"),
        client_secret_enc=crypto.encrypt("secret"),
        oauth_state_hash=None,
        oauth_state_nonce=None,
        oauth_state_session_id=None,
        oauth_state_expires_at=None,
        token_expires_at=datetime.now(UTC),
        connected_by_user_id=hr_user.id,
    )
    assert isinstance(http_client.token, FakeResponse)
    http_client.token._payload.pop("refresh_token")

    init = await service.initiate(hr_user)
    state = parse_qs(urlparse(init.redirect_url or "").query)["state"][0]
    result = await service.callback(hr=hr_user, state=state, code="code")

    assert result.status == "connected"
    assert connection_repo.state is not None
    assert crypto.decrypt(connection_repo.state.refresh_token_enc) == "existing-refresh"
    service._sync_cursor_repo.clear_cursor.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_legacy_grants_are_revoked_and_connection_requires_reauthorization(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    connection_repo: DurableConnectionRepo,
    oauth_grant_repo: AsyncMock,
    sync_cursor_repo: AsyncMock,
    crypto: CryptoUtils,
) -> None:
    connection_repo.state = SimpleNamespace(
        status="connected",
        email="legacy@example.com",
        google_sub="legacy-sub",
        email_domain="example.com",
        selected_calendar_id="calendar",
        credential_format_version=1,
        credential_key_version=1,
        access_token_enc=crypto.encrypt("access"),
        refresh_token_enc=crypto.encrypt("refresh"),
        client_secret_enc=crypto.encrypt("secret"),
        oauth_state_hash=None,
        oauth_state_nonce=None,
        oauth_state_session_id=None,
        oauth_state_expires_at=None,
        token_expires_at=datetime.now(UTC),
        connected_by_user_id=hr_user.id,
    )
    oauth_grant_repo.revoke_all = AsyncMock(return_value=[hr_user.id])

    result = await service.get_status()

    assert result.status == "reauthorization_required"
    assert result.email is None
    assert result.has_secret is False
    oauth_grant_repo.revoke_all.assert_awaited_once()
    sync_cursor_repo.clear_cursor.assert_awaited_once_with()
    assert connection_repo.state is not None
    assert connection_repo.state.access_token_enc is None
    assert connection_repo.state.refresh_token_enc is None

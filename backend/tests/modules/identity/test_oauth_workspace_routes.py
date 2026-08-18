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
from src.modules.identity.domain.exceptions import (
    DomainAccessDeniedError,
    GoogleAuthError,
    InvalidStateError,
)
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
async def test_disconnect_posts_revoke_and_clears_sync_cursor(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    http_client: FakeHttpClient,
    crypto: CryptoUtils,
) -> None:
    """Pins the success path only (revoke returns 200, the fixture default).

    Disconnect is no longer best-effort -- a failed revoke raises instead of
    proceeding silently. The failure branches (transport error, non-2xx
    without an exception, and the 400-is-still-success case) live in the
    dedicated tests below, not here.
    """
    service._connection_repo.get_singleton = AsyncMock(
        return_value=SimpleNamespace(refresh_token_enc=crypto.encrypt("refresh"))
    )
    await service.disconnect(hr_user)
    assert any(url == GOOGLE_REVOKE_URL for url, _ in http_client.posts)
    service._sync_cursor_repo.clear_cursor.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_disconnect_with_no_existing_grant_skips_revoke_and_audits_that(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    http_client: FakeHttpClient,
    connection_repo: DurableConnectionRepo,
    audit_service: AsyncMock,
) -> None:
    """No stored grant means nothing to revoke -- that must show up as its own value.

    Pins the ``"not_attempted"`` audit outcome so a future edit can't quietly
    rename or drop it without a test noticing.
    """
    connection_repo.state = None

    result = await service.disconnect(hr_user)

    assert result.status == "disconnected"
    assert not any(url == GOOGLE_REVOKE_URL for url, _ in http_client.posts)
    details = audit_service.log_action.await_args.kwargs["details"]
    assert details == {"result": "disconnected", "google_revoke": "not_attempted"}


@pytest.mark.asyncio
async def test_disconnect_confirmed_revoke_audits_true_result(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    connection_repo: DurableConnectionRepo,
    audit_service: AsyncMock,
    crypto: CryptoUtils,
) -> None:
    """A 200 from Google's /revoke is what actually licenses "disconnected"."""
    connection_repo.state = SimpleNamespace(refresh_token_enc=crypto.encrypt("refresh"))

    result = await service.disconnect(hr_user)

    assert result.status == "disconnected"
    assert connection_repo.disconnect_calls == 1
    details = audit_service.log_action.await_args.kwargs["details"]
    assert details == {"result": "disconnected", "google_revoke": "revoked"}


@pytest.mark.asyncio
async def test_disconnect_raises_when_revoke_request_transport_fails(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    http_client: FakeHttpClient,
    connection_repo: DurableConnectionRepo,
    audit_service: AsyncMock,
    crypto: CryptoUtils,
) -> None:
    """A transport error must not report "disconnected" (#384 lỗ 1).

    Google's grant may still be live -- ``except Exception: pass`` used to
    swallow this and audit a disconnect that never happened.
    """
    connection_repo.state = SimpleNamespace(refresh_token_enc=crypto.encrypt("refresh"))
    http_client.revoke = ConnectionError("boom")

    with pytest.raises(GoogleAuthError):
        await service.disconnect(hr_user)

    audit_service.log_action.assert_not_awaited()
    assert connection_repo.disconnect_calls == 0
    assert connection_repo.state is not None


@pytest.mark.asyncio
async def test_disconnect_raises_when_revoke_returns_500_without_exception(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    http_client: FakeHttpClient,
    connection_repo: DurableConnectionRepo,
    audit_service: AsyncMock,
    crypto: CryptoUtils,
) -> None:
    """A non-2xx Google response raises nothing in bare httpx -- this is #384 lỗ 2.

    ``self._http_client`` never calls ``raise_for_status()``, so a plain 500
    from Google produced no exception at all: the ``except Exception: pass``
    handler was never even entered, and the audit was written regardless. The
    fix has to check ``status_code`` explicitly, not just add a log to the
    handler.
    """
    connection_repo.state = SimpleNamespace(refresh_token_enc=crypto.encrypt("refresh"))
    http_client.revoke = FakeResponse(500, {})

    with pytest.raises(GoogleAuthError):
        await service.disconnect(hr_user)

    audit_service.log_action.assert_not_awaited()
    assert connection_repo.disconnect_calls == 0
    assert connection_repo.state is not None


@pytest.mark.asyncio
async def test_disconnect_treats_already_revoked_token_as_success(
    service: OrganizationGoogleConnectionService,
    hr_user: User,
    http_client: FakeHttpClient,
    connection_repo: DurableConnectionRepo,
    audit_service: AsyncMock,
    crypto: CryptoUtils,
) -> None:
    """Google returns 400 for a token that's already invalid/expired/revoked.

    Treating that as a failure would trap the user: they could never
    disconnect a connection that's already dead at Google. The grant is
    already gone either way, so this must still succeed -- distinctly
    recorded, not a bare "disconnected".
    """
    connection_repo.state = SimpleNamespace(refresh_token_enc=crypto.encrypt("refresh"))
    http_client.revoke = FakeResponse(400, {})

    result = await service.disconnect(hr_user)

    assert result.status == "disconnected"
    assert connection_repo.disconnect_calls == 1
    details = audit_service.log_action.await_args.kwargs["details"]
    assert details == {"result": "disconnected", "google_revoke": "already_revoked"}


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

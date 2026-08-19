"""The `hd` domain gate is wired to the real callback route, not just the service (#417).

Every existing case for this gate (``test_oauth_workspace_routes.py``) instantiates
``OrganizationGoogleConnectionService`` directly and calls ``service.callback(...)``.
None of them go through ``router.py``'s ``callback_google_connection`` /
``callback_google_connection_redirect`` handlers, so none would notice if a future
edit stopped the handler from calling ``connection_service.callback`` at all, or if
``_get_connection_service`` stopped wiring a real ``org_settings_repo`` into it.
That is exactly the shape of bug commit 8336676 introduced elsewhere in this module
(it deleted the call site, not the callee) and no test went red.

This module drives the real FastAPI router with ``TestClient``, the real
``get_db_session`` generator against Postgres (only the session *factory* is
monkeypatched, per the trap ``WORKSPACE_PROTOCOL.md`` names -- overriding
``get_db_session`` itself would hide the same teardown these tests need live), and
mocks only the HTTP boundary going out to Google: the OAuth discovery check inside
``OAuthConfigManager.validate_credentials``, the token exchange, and the userinfo
call.

Proof by mutation (#417): comment out the ``hd`` check
(``organization_google_connection_service.py:293-294``) and
``test_callback_rejects_email_outside_allowed_domain_end_to_end`` goes red --
the response turns from 403 into a 200 that connects an unauthorized domain.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.modules.identity.api.error_handler import register_auth_error_handlers
from src.modules.identity.api.router import router
from src.modules.identity.application.oauth_config_manager import OAuthConfigManager
from src.modules.identity.application.organization_google_connection_service import (
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    REQUIRED_SCOPES,
)
from src.modules.identity.container import get_current_user
from src.modules.identity.domain.entities import User, UserRole
from src.modules.recruitment.infrastructure.org_settings_repository import (
    OrganizationSettingsRepository,
)
from tests.conftest import _create_probe_database

pytestmark = pytest.mark.integration

_REDIRECT_URI = "https://app.example.com/api/auth/callback"
_ALLOWED_DOMAIN = "example.com"
_SCOPE = " ".join(REQUIRED_SCOPES)


@pytest.fixture(scope="module")
def probe_db_url(postgres_async_url: str) -> str:
    """A database this module alone writes to (mirrors #320's probe pattern)."""
    return _create_probe_database(postgres_async_url, "org_google_connection_domain_gate_probe")


@pytest_asyncio.fixture
async def hr_user(probe_db_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[User]:
    """Point the real ``get_db_session`` at the probe database and seed org state.

    Only the session *factory* is swapped -- ``get_db_session`` itself, including
    its ``yield session; await session.commit()`` body, runs exactly as it does in
    production. That is what lets the mutation in #417 actually reach the gate.
    """
    engine = create_async_engine(probe_db_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(
        "src.modules.identity.container._get_async_session_maker",
        lambda: maker,
    )

    user = User(
        id=uuid4(),
        email="hr@example.com",
        name="HR",
        role=UserRole.HR,
        password_hash="hashed",
    )
    async with maker() as session:
        await session.execute(text("DELETE FROM audit_logs"))
        await session.execute(text("DELETE FROM sync_cursors"))
        await session.execute(text("DELETE FROM organization_google_connections"))
        await session.execute(text("DELETE FROM oauth_configs"))
        await session.execute(text("DELETE FROM organization_settings"))
        await session.execute(text("DELETE FROM users"))
        session.add(user)
        await session.commit()
        await OrganizationSettingsRepository(session).set_allowed_domains([_ALLOWED_DOMAIN])

    try:
        yield user
    finally:
        await engine.dispose()


@pytest.fixture
def app(hr_user: User, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """The real auth router with only the outbound-to-Google discovery check swapped."""

    async def _always_valid(_self: OAuthConfigManager, _client_id: str) -> bool:
        return True

    monkeypatch.setattr(OAuthConfigManager, "validate_credentials", _always_valid)

    application = FastAPI()
    application.include_router(router)
    register_auth_error_handlers(application)
    application.dependency_overrides[get_current_user] = lambda: hr_user
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _configure_google(client: TestClient) -> None:
    response = client.post(
        "/api/auth/organization-google-connection",
        json={
            "client_id": "417-domain-gate.apps.googleusercontent.com",
            "client_secret": "super-secret-6789",
            "redirect_uri": _REDIRECT_URI,
        },
    )
    assert response.status_code == 200, response.text


def _obtain_state(client: TestClient) -> str:
    response = client.get("/api/auth/organization-google-connection/authorize-url")
    assert response.status_code == 200, response.text
    redirect_url = response.json()["redirect_url"]
    return parse_qs(urlparse(redirect_url).query)["state"][0]


def _mock_google_consent(*, hd: str | None, email: str) -> None:
    userinfo: dict[str, Any] = {"email": email, "sub": "sub-417"}
    if hd is not None:
        userinfo["hd"] = hd
    respx.post(GOOGLE_TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "access-417",
                "refresh_token": "refresh-417",
                "scope": _SCOPE,
                "expires_in": 3600,
            },
        )
    )
    respx.get(GOOGLE_USERINFO_URL).mock(return_value=httpx.Response(200, json=userinfo))


@respx.mock
def test_callback_rejects_email_outside_allowed_domain_end_to_end(client: TestClient) -> None:
    """The route, not just the service, must refuse a non-member Workspace domain.

    Proof by mutation (#417): delete the ``hd`` check at
    ``organization_google_connection_service.py:293-294`` and this turns from 403
    into 200 with ``status: connected`` -- the mutation is caught here, at the
    route the browser actually calls, not only at the service unit tests already
    covered.
    """
    _configure_google(client)
    state = _obtain_state(client)
    _mock_google_consent(hd="attacker.com", email="eve@attacker.com")

    response = client.post(
        "/api/auth/organization-google-connection/callback",
        json={"code": "auth-code", "state": state},
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "DOMAIN_NOT_ALLOWED"


@respx.mock
def test_callback_accepts_email_inside_allowed_domain_end_to_end(client: TestClient) -> None:
    """The companion case: the real DI wiring still lets the legitimate domain through.

    Without this, a broken guard that always raises ``DomainAccessDeniedError``
    would make the rejection test above pass for the wrong reason.
    """
    _configure_google(client)
    state = _obtain_state(client)
    _mock_google_consent(hd=_ALLOWED_DOMAIN, email=f"hr@{_ALLOWED_DOMAIN}")

    response = client.post(
        "/api/auth/organization-google-connection/callback",
        json={"code": "auth-code", "state": state},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "connected"
    assert body["email"] == f"hr@{_ALLOWED_DOMAIN}"

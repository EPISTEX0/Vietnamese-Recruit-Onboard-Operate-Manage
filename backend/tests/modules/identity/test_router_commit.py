"""Every writing endpoint on the auth router commits before it returns (#320).

``get_db_session`` commits *after* ``yield``, and FastAPI drains that stack only
once the response has already been sent. A write that relies on the teardown
alone therefore answers 200 before the transaction is durable: a later read can
still see the old row, and a teardown ``commit()`` that fails rolls the write
back with the client already holding a success.

#312 established this convention for ``admin_router``. This module carries it to
``router.py``, where twelve endpoints were still teardown-only. Three
differences from the ``admin_router`` case shaped the tests below.

**Two of the twelve are ``GET``s.** ``GET /callback`` completes OAuth consent
and ``GET /organization-google-connection`` revokes legacy grants. So
``test_every_endpoint_is_classified`` partitions *every* route on the router
rather than filtering by HTTP method the way #312 could -- a method filter would
wave both of them through.

**Audit lives under the handler, not beside it.** In ``admin_router`` the
handler called ``audit_service.log_action`` itself, so #312 could assert
``[audit, commit]`` directly. Here the audit calls sit inside
``OrganizationGoogleConnectionService`` (``callback``, ``disconnect``,
``update_selected_calendar``), so the handler cannot see them. The assertion is
therefore that ``commit()`` is the *last* thing the handler does, after every
service call it makes -- which subsumes the ordering #312 spelled out, because
the audit write happens inside a call that has to precede the commit.

**Two writing endpoints correctly commit below the handler** and so must *not*
grow a second commit; ``SERVICE_COMMITS`` pins those commits where they live.

These tests call the route handlers directly rather than through
``TestClient``. That is deliberate: driving them over HTTP would need
``dependency_overrides[get_db_session]``, which replaces the very generator
whose late commit is the bug -- the seam would disappear from the test. Calling
the handler and inspecting the session it was handed keeps the commit
observable.

Every assertion here is made *after* the handler coroutine has completed, so
anything recorded is by construction something the handler did before it
returned.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call
from uuid import uuid4

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.identity.api import router as router_module
from src.modules.identity.api.router import (
    CalendarListResponseSchema,
    SelectCalendarRequest,
    authorize_google_connection,
    callback_google_connection,
    callback_google_connection_redirect,
    change_password,
    disconnect_google_connection,
    forgot_password,
    get_google_connection,
    local_login,
    logout,
    reconnect_google_connection,
    router,
    save_google_connection_config,
    save_selected_calendar,
)
from src.modules.identity.api.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    GoogleWorkspaceCallbackRequest,
    OAuthConfigUpdateRequest,
)
from src.modules.identity.application.auth_service import AuthService, LocalAuthResult
from src.modules.identity.application.organization_google_connection_service import (
    OrganizationGoogleConnectionResponse,
)
from src.modules.identity.application.password_reset_service import PasswordResetService
from src.modules.identity.container import get_db_session
from src.modules.identity.domain.entities import User, UserRole

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures shared by every case
# ---------------------------------------------------------------------------


def _user(role: UserRole = UserRole.HR, email: str = "hr@example.com") -> User:
    return User(id=uuid4(), email=email, name="HR", role=role, password_hash="hashed")


_HR = _user()

#: What the connection service hands back. ``status`` has to be one of the
#: literals ``GoogleWorkspaceConnectionResponse`` accepts, since the handlers
#: splat this straight into it.
_CONNECTION = OrganizationGoogleConnectionResponse(status="disconnected")

_AUTH_RESULT = LocalAuthResult(
    access_token="access-token",
    refresh_token="refresh-token",
    user=_HR,
    must_change_password=False,
)


@dataclass
class _Timeline:
    """A session and a set of services that record their calls in one order.

    Both delegate to the same ``calls`` mock, so ``calls.mock_calls`` is the
    interleaved sequence -- which is what the ordering assertion needs, and
    what asserting on the two mocks separately could not show.
    """

    session: AsyncMock
    calls: MagicMock


def _timeline() -> _Timeline:
    calls = MagicMock()
    session = AsyncMock(spec=AsyncSession)
    session.commit.side_effect = lambda: calls.commit()
    return _Timeline(session=session, calls=calls)


def _records(timeline: _Timeline, result: Any) -> AsyncMock:
    """A service method that logs ``work`` on the timeline when awaited.

    The label is deliberately uniform: which service ran is already pinned by
    the case, and what these tests assert is ordering -- that nothing the
    handler delegates happens *after* the commit.
    """

    async def _call(*_args: Any, **_kwargs: Any) -> Any:
        timeline.calls.work()
        return result

    return AsyncMock(side_effect=_call)


def _connection_service(timeline: _Timeline) -> AsyncMock:
    """An ``OrganizationGoogleConnectionService`` whose every method records."""
    service = AsyncMock()
    for method in (
        "get_status",
        "initiate",
        "callback",
        "disconnect",
        "update_selected_calendar",
    ):
        setattr(service, method, _records(timeline, _CONNECTION))
    return service


def _request() -> MagicMock:
    """A ``Request`` carrying the client IP and refresh cookie the handlers read."""
    request = MagicMock()
    request.client.host = "198.51.100.7"
    request.cookies = {"refresh_token": "raw-refresh-token"}
    return request


def _rate_limiter() -> AsyncMock:
    limiter = AsyncMock()
    limiter.check_rate_limit = AsyncMock(return_value=True)
    limiter.check_rate_limit_for = AsyncMock(return_value=True)
    return limiter


@pytest.fixture(autouse=True)
def _frontend_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """``callback_google_connection_redirect`` builds its redirect from settings.

    Only the module-global lookup inside that handler is swapped; the
    ``Depends(get_settings)`` wiring the router declares elsewhere is untouched,
    so this cannot mask a session dependency.
    """
    monkeypatch.setattr(
        router_module,
        "get_settings",
        lambda: SimpleNamespace(frontend_url="https://app.example.com/"),
    )


# ---------------------------------------------------------------------------
# One case per writing endpoint
# ---------------------------------------------------------------------------

_Case = Callable[[_Timeline], Awaitable[object]]


async def _save_google_connection_config(t: _Timeline) -> object:
    manager = AsyncMock()
    manager.update_config = _records(t, None)
    return await save_google_connection_config(
        OAuthConfigUpdateRequest(
            client_id="client-id",
            client_secret="secret-6789",
            redirect_uri="https://app.example.com/api/auth/callback",
        ),
        _HR,
        manager,
        connection_service=_connection_service(t),
        session=t.session,
    )


async def _authorize_google_connection(t: _Timeline) -> object:
    return await authorize_google_connection(
        _HR,
        connection_service=_connection_service(t),
        session=t.session,
    )


async def _get_google_connection(t: _Timeline) -> object:
    return await get_google_connection(
        _HR,
        connection_service=_connection_service(t),
        session=t.session,
    )


async def _reconnect_google_connection(t: _Timeline) -> object:
    return await reconnect_google_connection(
        _HR,
        connection_service=_connection_service(t),
        session=t.session,
    )


async def _callback_google_connection_redirect(t: _Timeline) -> object:
    return await callback_google_connection_redirect(
        "auth-code",
        "state-token",
        _HR,
        connection_service=_connection_service(t),
        session=t.session,
    )


async def _disconnect_google_connection(t: _Timeline) -> object:
    return await disconnect_google_connection(
        _HR,
        connection_service=_connection_service(t),
        session=t.session,
    )


async def _callback_google_connection(t: _Timeline) -> object:
    return await callback_google_connection(
        GoogleWorkspaceCallbackRequest(code="auth-code", state="state-token"),
        _HR,
        connection_service=_connection_service(t),
        session=t.session,
    )


async def _save_selected_calendar(t: _Timeline) -> object:
    return await save_selected_calendar(
        SelectCalendarRequest(calendar_id="primary"),
        _HR,
        connection_service=_connection_service(t),
        session=t.session,
    )


async def _local_login(t: _Timeline) -> object:
    auth_service = AsyncMock()
    auth_service.login = _records(t, _AUTH_RESULT)
    return await local_login(
        _request(),
        router_module.AuthLoginRequest(email="hr@example.com", password="password-1234"),
        auth_service,
        _rate_limiter(),
        session=t.session,
    )


async def _change_password(t: _Timeline) -> object:
    auth_service = AsyncMock()
    auth_service.change_password = _records(t, _AUTH_RESULT)
    return await change_password(
        ChangePasswordRequest(current_password="old-password", new_password="new-password-1234"),
        _HR,
        auth_service,
        session=t.session,
    )


async def _logout(t: _Timeline) -> object:
    auth_service = AsyncMock()
    auth_service.logout = _records(t, None)
    return await logout(
        _request(),
        auth_service,
        session=t.session,
    )


async def _forgot_password(t: _Timeline) -> object:
    reset_service = AsyncMock()
    reset_service.create_reset_token = _records(t, True)
    settings = SimpleNamespace(
        rate_limit_forgot_password_ip_max=3,
        rate_limit_forgot_password_ip_window_seconds=900,
        rate_limit_forgot_password_email_max=2,
        rate_limit_forgot_password_email_window_seconds=900,
    )
    return await forgot_password(
        _request(),
        ForgotPasswordRequest(email="hr@example.com"),
        reset_service,
        _rate_limiter(),
        settings,
        session=t.session,
    )


#: One entry per endpoint that writes and has nothing below it that commits,
#: keyed by the handler's own ``__name__`` so
#: ``test_every_endpoint_is_classified`` can prove nothing is missing. A diff of
#: this shape fails by omitting an endpoint, not by getting one wrong, so the
#: classification check is the load-bearing part.
WRITE_CASES: dict[str, _Case] = {
    "save_google_connection_config": _save_google_connection_config,
    "authorize_google_connection": _authorize_google_connection,
    "get_google_connection": _get_google_connection,
    "reconnect_google_connection": _reconnect_google_connection,
    "callback_google_connection_redirect": _callback_google_connection_redirect,
    "disconnect_google_connection": _disconnect_google_connection,
    "callback_google_connection": _callback_google_connection,
    "save_selected_calendar": _save_selected_calendar,
    "local_login": _local_login,
    "change_password": _change_password,
    "logout": _logout,
    "forgot_password": _forgot_password,
}

#: Endpoints that write but whose service already commits, so the handler must
#: *not* add a second one. Mapped to the method that owns the commit, which
#: ``test_service_owned_commits_stay_where_they_are`` pins -- without that pin,
#: deleting the commit down there would silently move these into the bug class
#: with nothing here to notice.
SERVICE_COMMITS: dict[str, tuple[type, str]] = {
    "setup": (AuthService, "setup_first_run"),
    "reset_password": (PasswordResetService, "reset_password"),
}

#: Endpoints that touch no table at all. ``refresh`` is a POST that only reads a
#: refresh token and signs a new JWT; ``list_calendars_for_selection`` and
#: ``me`` take a session but issue nothing but ``SELECT``s.
READ_ONLY_ENDPOINTS = frozenset(
    {
        "setup_status",
        "list_calendars_for_selection",
        "reset_password_token_info",
        "refresh",
        "me",
        "grant_status",
    }
)


def _endpoint_names() -> set[str]:
    return {route.endpoint.__name__ for route in router.routes if isinstance(route, APIRoute)}


def _routes_for(name: str) -> list[APIRoute]:
    return [
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.endpoint.__name__ == name
    ]


def _walk(dependant: Dependant) -> Iterator[Dependant]:
    yield dependant
    for sub in dependant.dependencies:
        yield from _walk(sub)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_every_endpoint_is_classified() -> None:
    """No endpoint may be silently left out of the commit convention.

    Unlike #312's equivalent, this looks at every route rather than only the
    mutating HTTP methods: two of the writers here are ``GET``s, so a method
    filter would report full coverage while missing them.
    """
    names = _endpoint_names()

    assert names == set(WRITE_CASES) | set(SERVICE_COMMITS) | READ_ONLY_ENDPOINTS
    assert len(names) == 20


@pytest.mark.parametrize("name", sorted(WRITE_CASES))
def test_write_endpoint_commits_the_session_its_services_use(name: str) -> None:
    """The handler's session is the one the services below it write through.

    ``test_write_endpoint_commits_before_returning`` hands the handler a
    session directly, so on its own it only proves *a* session was committed.
    What makes that the right session is FastAPI's per-request dependency
    cache: the handler and every service provider under it request the same
    ``get_db_session`` callable with ``use_cache``, so one session is built and
    shared. Assert that wiring, or a provider could later grow a session of its
    own and every case above would still pass while the write quietly fell back
    to the teardown.
    """
    for route in _routes_for(name):
        session_deps = [d for d in _walk(route.dependant) if d.call is get_db_session]

        assert session_deps, f"{name} resolves no session at all"
        assert all(d.use_cache for d in session_deps), (
            f"{name} has an uncached session dependency, so it would get a second session"
        )


@pytest.mark.parametrize("name", sorted(WRITE_CASES))
async def test_write_endpoint_commits_before_returning(name: str) -> None:
    """The handler commits its own transaction, and does so last.

    Ordering matters twice over: the commit has to follow the writes it makes
    durable, and for the three connection endpoints that audit internally it
    has to follow the audit row too. Both reduce to "no service call after the
    commit", because the audit happens inside a service call.
    """
    timeline = _timeline()

    await WRITE_CASES[name](timeline)

    calls = timeline.calls.mock_calls
    assert call.commit() in calls, f"{name} never commits; it relies on the teardown"
    assert calls.count(call.commit()) == 1, f"{name} commits more than once: {calls}"
    assert len(calls) > 1, f"{name} committed without doing any work: {calls}"
    assert calls[-1] == call.commit(), f"{name} does more work after committing: {calls}"


@pytest.mark.parametrize("name", sorted(SERVICE_COMMITS))
def test_service_owned_commits_stay_where_they_are(name: str) -> None:
    """The two service-level commits still exist, so the handler need not add one.

    This is a source-level pin, not a behavioural proof: it fails if the commit
    is deleted or renamed, which is exactly the change that would move these
    endpoints into ``WRITE_CASES`` without anything else noticing.
    """
    owner, method = SERVICE_COMMITS[name]
    source = inspect.getsource(getattr(owner, method))

    assert "commit()" in source, f"{owner.__name__}.{method} no longer commits for {name}"

    handler_source = inspect.getsource(getattr(router_module, name))
    assert "session.commit()" not in handler_source, (
        f"{name} commits in the handler as well as in {owner.__name__}.{method}"
    )


@pytest.mark.parametrize("name", sorted(READ_ONLY_ENDPOINTS))
def test_read_only_endpoints_do_not_commit(name: str) -> None:
    """Read-only handlers stay read-only.

    ``me`` and ``list_calendars_for_selection`` legitimately take a session to
    read through, so "has no session" cannot be the check the way it was for
    #312's two probe endpoints. Committing is what would be wrong here.
    """
    source = inspect.getsource(getattr(router_module, name))

    assert "commit()" not in source, f"{name} is classified read-only but commits"


def test_calendar_list_schema_is_unaffected() -> None:
    """The read-only calendars endpoint keeps returning its own schema.

    Guards the one endpoint in this file that already took a session before
    #320, so a careless sweep that added a commit everywhere would show up
    here rather than in production.
    """
    assert CalendarListResponseSchema.model_fields.keys() == {
        "calendars",
        "selected_calendar_id",
    }

"""Every writing endpoint on ``admin_router`` commits before it returns (#312).

``get_db_session`` commits *after* ``yield``, and FastAPI drains that stack only
once the response has already been sent. A write that relies on the teardown
alone therefore answers 200 before the transaction is durable: a later read can
still see the old row, and a teardown ``commit()`` that fails rolls the write
back with the client already holding a success.

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
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call
from uuid import uuid4

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.gmail.application.classification_rollout import (
    BusinessPolicy,
    RolloutMode,
)
from src.modules.identity.api import admin_router as admin_router_module
from src.modules.identity.api.admin_router import (
    activate_org_api_key,
    add_domains,
    admin_router,
    change_user_role,
    configure_classification_rollout,
    create_staff_account,
    enforce_classification_guardrails,
    remove_domain,
    replace_domains,
    revoke_org_api_key,
    rollback_classification_rollout,
    set_credential_source,
    update_assistant_tools,
    update_oauth_config,
    update_organization_ai_config,
    update_provider_config,
)
from src.modules.identity.api.admin_schemas import (
    ActivateOrgApiKeyRequest,
    AssistantToolConfigUpdateRequest,
    ClassificationReleaseMetricsRequest,
    ClassificationRolloutRequest,
    DomainAddRequest,
    DomainReplaceRequest,
    OrganizationAIConfigurationRequest,
    OrganizationAIConfigurationResponse,
    RoleUpdateRequest,
    SetCredentialSourceRequest,
    StaffAccountCreateRequest,
    UpdateProviderConfigRequest,
)
from src.modules.identity.api.schemas import (
    OAuthConfigResponse,
    OAuthConfigUpdateRequest,
)
from src.modules.identity.application.audit_service import AuditService
from src.modules.identity.container import get_db_session
from src.modules.identity.domain.entities import (
    User,
    UserRole,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures shared by every case
# ---------------------------------------------------------------------------


def _user(role: UserRole = UserRole.SYSTEM_ADMIN, email: str = "admin@example.com") -> User:
    return User(id=uuid4(), email=email, name="Admin", role=role)


_ADMIN = _user()

# The response model doubles as a valid stand-in for the service's view object:
# it carries exactly the attributes ``_ai_view_response`` reads, already typed.
_VIEW = OrganizationAIConfigurationResponse(
    provider="openai",
    base_url="https://api.example.com/v1",
    model="gpt-4o-mini",
    api_key_masked="****1234",
    configured=True,
    updated_at=None,
)


@dataclass(frozen=True)
class _AIResult:
    """Stands in for the service result objects the AI-config handlers unwrap."""

    view: OrganizationAIConfigurationResponse
    audit_details: dict[str, Any]


_AI_RESULT = _AIResult(view=_VIEW, audit_details={"field": "value"})


def _ai_service(method: str) -> AsyncMock:
    """An OrganizationAIConfigService whose ``method`` returns a usable result."""
    service = AsyncMock()
    setattr(service, method, AsyncMock(return_value=_AI_RESULT))
    return service


@dataclass
class _Timeline:
    """An audit service and a session that record their calls in one order.

    Both delegate to the same ``calls`` mock, so ``calls.mock_calls`` is the
    interleaved sequence -- which is what the ordering assertion needs, and
    what asserting on the two mocks separately could not show.
    """

    session: AsyncMock
    audit: AsyncMock
    calls: MagicMock


def _timeline() -> _Timeline:
    calls = MagicMock()
    session = AsyncMock(spec=AsyncSession)
    session.commit.side_effect = lambda: calls.commit()
    audit = AsyncMock(spec=AuditService)
    audit.log_action.side_effect = lambda **_: calls.audit()
    return _Timeline(session=session, audit=audit, calls=calls)


# ---------------------------------------------------------------------------
# One case per writing endpoint
# ---------------------------------------------------------------------------

_Case = Callable[[_Timeline], Awaitable[object]]


def _ai_case(handler: Any, method: str, *body: Any, **kwargs: Any) -> _Case:
    """Build the case for one AI-configuration handler.

    These eight handlers are variations on a single shape: an optional
    request body, an ``OrganizationAIConfigService`` whose one method returns a
    view plus audit details, then the audit call and the commit. Spelling each
    out as its own closure duplicated that shape eight times and buried the
    only part that differs -- which service method the handler drives.

    (The eight HR-owned consent/toggle handlers on the same shape moved to
    ``hr_ai_config_router`` in #420; see
    ``test_hr_ai_config_router_commit.py`` for their coverage.)
    """

    async def case(t: _Timeline) -> object:
        return await handler(
            *body,
            _ADMIN,
            **kwargs,
            service=_ai_service(method),
            audit_service=t.audit,
            session=t.session,
        )

    return case


async def _create_staff_account(t: _Timeline) -> object:
    auth_service = AsyncMock()
    auth_service.create_staff_account = AsyncMock(
        return_value=(
            _user(role=UserRole.HR, email="hr@example.com"),
            "http://localhost:3000/reset-password?token=abc123",
        )
    )
    return await create_staff_account(
        StaffAccountCreateRequest(email="hr@example.com", name="HR", role=UserRole.HR),
        _ADMIN,
        auth_service=auth_service,
        audit_service=t.audit,
        session=t.session,
    )


async def _change_user_role(t: _Timeline) -> object:
    role_service = AsyncMock()
    role_service.change_role = AsyncMock(
        return_value=(_user(role=UserRole.HR, email="target@example.com"), UserRole.USER)
    )
    return await change_user_role(
        uuid4(),
        RoleUpdateRequest(role=UserRole.HR),
        _ADMIN,
        role_service=role_service,
        audit_service=t.audit,
        session=t.session,
    )


async def _update_oauth_config(t: _Timeline) -> object:
    manager = AsyncMock()
    manager.update_config = AsyncMock(
        return_value=OAuthConfigResponse(
            client_id="client-id",
            client_secret_masked="****6789",
            redirect_uri="https://app.example.com/callback",
            updated_at=None,
            source="database",
        )
    )
    return await update_oauth_config(
        OAuthConfigUpdateRequest(
            client_id="client-id",
            client_secret="secret-6789",
            redirect_uri="https://app.example.com/callback",
        ),
        _ADMIN,
        oauth_manager=manager,
        audit_service=t.audit,
        session=t.session,
    )


async def _add_domains(t: _Timeline) -> object:
    repo = AsyncMock()
    repo.add_domains = AsyncMock(return_value=["company.vn"])
    return await add_domains(
        DomainAddRequest(domains=["company.vn"]),
        _ADMIN,
        org_repo=repo,
        audit_service=t.audit,
        session=t.session,
    )


async def _replace_domains(t: _Timeline) -> object:
    repo = AsyncMock()
    repo.set_allowed_domains = AsyncMock(return_value=["company.vn"])
    return await replace_domains(
        DomainReplaceRequest(domains=["company.vn"]),
        _ADMIN,
        org_repo=repo,
        audit_service=t.audit,
        session=t.session,
    )


async def _remove_domain(t: _Timeline) -> object:
    repo = AsyncMock()
    repo.remove_domain = AsyncMock(return_value=[])
    return await remove_domain(
        "company.vn",
        _ADMIN,
        org_repo=repo,
        audit_service=t.audit,
        session=t.session,
    )


async def _update_assistant_tools(t: _Timeline) -> object:
    repo = AsyncMock()
    repo.get_all = AsyncMock(return_value=[])
    return await update_assistant_tools(
        AssistantToolConfigUpdateRequest(tools={}),
        _ADMIN,
        tool_config_repo=repo,
        audit_service=t.audit,
        session=t.session,
    )


#: One entry per writing endpoint, keyed by the handler's own ``__name__`` so
#: ``test_every_mutating_endpoint_is_accounted_for`` can prove nothing is
#: missing. A diff of this shape fails by omitting an endpoint, not by getting
#: one wrong, so the coverage check is the load-bearing part.
WRITE_CASES: dict[str, _Case] = {
    "configure_classification_rollout": _ai_case(
        configure_classification_rollout,
        "configure_classification_rollout",
        ClassificationRolloutRequest(
            mode=RolloutMode.STABLE,
            business_policy=BusinessPolicy.RECALL_FIRST,
            policy_version="recall-first-v1",
            classifier_version="classifier-v1",
        ),
    ),
    "enforce_classification_guardrails": _ai_case(
        enforce_classification_guardrails,
        "enforce_classification_guardrails",
        ClassificationReleaseMetricsRequest(
            job_application_recall=0.9,
            baseline_recall=0.85,
            needs_classification_rate=0.1,
            correction_rate=0.01,
            review_rate=0.2,
            p95_latency_ms=1200,
            provider_error_rate=0.0,
            duplicate_count=0,
        ),
    ),
    "rollback_classification_rollout": _ai_case(
        rollback_classification_rollout, "rollback_classification_rollout"
    ),
    "update_organization_ai_config": _ai_case(
        update_organization_ai_config,
        "update",
        OrganizationAIConfigurationRequest(
            provider="openai",
            base_url="https://api.example.com/v1",
            model="gpt-4o-mini",
            api_key="sk-test",
        ),
    ),
    "set_credential_source": _ai_case(
        set_credential_source,
        "set_credential_source",
        SetCredentialSourceRequest(credential_source="org_api_key"),
    ),
    "activate_org_api_key": _ai_case(
        activate_org_api_key,
        "activate_org_api_key",
        ActivateOrgApiKeyRequest(api_key="sk-test"),
    ),
    "revoke_org_api_key": _ai_case(revoke_org_api_key, "revoke_org_api_key"),
    "update_provider_config": _ai_case(
        update_provider_config,
        "update_provider_config",
        UpdateProviderConfigRequest(
            provider="openai",
            base_url="https://api.example.com/v1",
            model="gpt-4o-mini",
        ),
    ),
    "create_staff_account": _create_staff_account,
    "change_user_role": _change_user_role,
    "update_oauth_config": _update_oauth_config,
    "add_domains": _add_domains,
    "replace_domains": _replace_domains,
    "remove_domain": _remove_domain,
    "update_assistant_tools": _update_assistant_tools,
}

#: Mutating by HTTP method, but they persist nothing: both only probe an
#: outbound provider connection (``test_connection`` /
#: ``test_deployment_key_connection`` validate a candidate and issue one
#: completion request), so there is no transaction for them to commit.
PROBE_ONLY_ENDPOINTS = frozenset(
    {
        "test_organization_ai_config",
        "test_deployment_key_connection",
    }
)

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _mutating_endpoint_names() -> set[str]:
    return {
        route.endpoint.__name__
        for route in admin_router.routes
        if isinstance(route, APIRoute) and route.methods & _MUTATING_METHODS
    }


def _routes_for(name: str) -> list[APIRoute]:
    return [
        route
        for route in admin_router.routes
        if isinstance(route, APIRoute) and route.endpoint.__name__ == name
    ]


def _walk(dependant: Dependant) -> Iterator[Dependant]:
    yield dependant
    for sub in dependant.dependencies:
        yield from _walk(sub)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_every_mutating_endpoint_is_accounted_for() -> None:
    """No endpoint may be silently left out of the commit convention."""
    names = _mutating_endpoint_names()

    assert names == set(WRITE_CASES) | PROBE_ONLY_ENDPOINTS
    assert len(names) == 17


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
    """The handler commits its own transaction, and does so after auditing.

    Ordering matters: the audit row belongs to the same transaction as the
    action it describes, so committing before ``log_action`` would leave the
    trail behind again.
    """
    timeline = _timeline()

    await WRITE_CASES[name](timeline)

    assert timeline.calls.mock_calls == [call.audit(), call.commit()]


@pytest.mark.parametrize("name", sorted(PROBE_ONLY_ENDPOINTS))
def test_probe_endpoints_take_no_session(name: str) -> None:
    """The two connection probes stay read-only, so they get no session."""
    endpoint = getattr(admin_router_module, name)
    assert "session" not in inspect.signature(endpoint).parameters


class TestTeardownSafetyNet:
    """``get_db_session`` keeps committing after ``yield`` (#312 keeps the net).

    Explicit commits are additive. Dropping the teardown would turn "endpoint
    forgot to commit" into silent data loss, which is worse than the late
    commit this ticket fixes.
    """

    async def test_commits_after_the_dependent_finishes(self, monkeypatch) -> None:
        session = AsyncMock(spec=AsyncSession)
        monkeypatch.setattr(
            "src.modules.identity.container._get_async_session_maker",
            lambda: _session_maker_returning(session),
        )

        generator = get_db_session()
        yielded = await anext(generator)
        assert yielded is session
        session.commit.assert_not_awaited()

        with pytest.raises(StopAsyncIteration):
            await anext(generator)
        session.commit.assert_awaited_once()

    async def test_rolls_back_and_reraises_on_failure(self, monkeypatch) -> None:
        session = AsyncMock(spec=AsyncSession)
        monkeypatch.setattr(
            "src.modules.identity.container._get_async_session_maker",
            lambda: _session_maker_returning(session),
        )

        generator = get_db_session()
        await anext(generator)

        with pytest.raises(RuntimeError, match="handler blew up"):
            await generator.athrow(RuntimeError("handler blew up"))
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()


def _session_maker_returning(session: AsyncMock) -> Callable[[], Any]:
    """Build a session factory usable as ``async with session_maker() as s``."""

    class _Maker:
        async def __aenter__(self) -> AsyncMock:
            return session

        async def __aexit__(self, *_: object) -> bool:
            return False

    return lambda: _Maker()

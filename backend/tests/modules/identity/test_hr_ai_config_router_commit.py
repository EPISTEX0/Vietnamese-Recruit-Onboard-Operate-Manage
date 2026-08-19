"""Every writing endpoint on ``hr_ai_config_router`` commits before it returns.

Sibling of ``test_admin_router_commit.py`` for the eight HR-owned AI-config
handlers that moved out of ``admin_router`` in #420 (data-policy consent,
capability consent, capability toggles, policy preset). Same convention, same
reasons: see that file's module docstring. The three read-only endpoints here
(``get_hr_ai_config``, ``get_data_policy``, ``get_provider_status``) take no
session and persist nothing, so they need no commit-ordering coverage --
consistent with how GET ``/organization/ai-config`` is untracked in the
admin-router suite.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call
from uuid import uuid4

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.identity.api import hr_ai_config_router as hr_router_module
from src.modules.identity.api.hr_ai_config_router import (
    accept_assistant_consent,
    accept_automation_consent,
    accept_data_policy,
    disable_ai_assistant,
    disable_ai_automation,
    enable_ai_assistant,
    enable_ai_automation,
    get_hr_ai_config,
    hr_ai_config_router,
    set_ai_policy_preset,
)
from src.modules.identity.application.audit_service import AuditService
from src.modules.identity.application.organization_ai_config_service import (
    AIConfigurationView,
    AIPolicyPreset,
)
from src.modules.identity.container import get_db_session
from src.modules.identity.domain.entities import User, UserRole

pytestmark = pytest.mark.unit


def _user(role: UserRole = UserRole.HR, email: str = "hr@example.com") -> User:
    return User(id=uuid4(), email=email, name="HR", role=role)


_HR = _user()

# The real service view, not the narrow response: ``_hr_ai_view_response``
# reads it by its own (wider) field names, e.g. ``configured``, not
# ``provider_configured``.
_VIEW = AIConfigurationView(
    provider="openai",
    base_url="https://api.example.com/v1",
    model="gpt-4o-mini",
    api_key_masked="****1234",
    configured=True,
    updated_at=None,
    data_policy_accepted=True,
    ai_automation_consent=True,
    ai_assistant_consent=True,
)


@dataclass(frozen=True)
class _AIResult:
    """Stands in for the service result objects the handlers unwrap."""

    view: AIConfigurationView
    audit_details: dict[str, Any]


_AI_RESULT = _AIResult(view=_VIEW, audit_details={"field": "value"})


def _ai_service(method: str) -> AsyncMock:
    service = AsyncMock()
    setattr(service, method, AsyncMock(return_value=_AI_RESULT))
    return service


@dataclass
class _Timeline:
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


_Case = Callable[[_Timeline], Awaitable[object]]


def _ai_case(handler: Any, method: str, *body: Any, **kwargs: Any) -> _Case:
    async def case(t: _Timeline) -> object:
        return await handler(
            *body,
            _HR,
            **kwargs,
            service=_ai_service(method),
            audit_service=t.audit,
            session=t.session,
        )

    return case


#: One entry per writing endpoint, keyed by the handler's own ``__name__`` so
#: ``test_every_mutating_endpoint_is_accounted_for`` can prove nothing is
#: missing.
WRITE_CASES: dict[str, _Case] = {
    "accept_data_policy": _ai_case(accept_data_policy, "accept_data_policy"),
    "accept_automation_consent": _ai_case(accept_automation_consent, "accept_automation_consent"),
    "accept_assistant_consent": _ai_case(accept_assistant_consent, "accept_assistant_consent"),
    "enable_ai_automation": _ai_case(enable_ai_automation, "enable_automation"),
    "disable_ai_automation": _ai_case(disable_ai_automation, "disable_automation"),
    "enable_ai_assistant": _ai_case(enable_ai_assistant, "enable_assistant"),
    "disable_ai_assistant": _ai_case(disable_ai_assistant, "disable_assistant"),
    "set_ai_policy_preset": _ai_case(
        set_ai_policy_preset, "set_policy_preset", preset=AIPolicyPreset.BALANCED
    ),
}

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _mutating_endpoint_names() -> set[str]:
    return {
        route.endpoint.__name__
        for route in hr_ai_config_router.routes
        if isinstance(route, APIRoute) and route.methods & _MUTATING_METHODS
    }


def _routes_for(name: str) -> list[APIRoute]:
    return [
        route
        for route in hr_ai_config_router.routes
        if isinstance(route, APIRoute) and route.endpoint.__name__ == name
    ]


def _walk(dependant: Dependant):
    yield dependant
    for sub in dependant.dependencies:
        yield from _walk(sub)


def test_every_mutating_endpoint_is_accounted_for() -> None:
    """No endpoint may be silently left out of the commit convention."""
    names = _mutating_endpoint_names()

    assert names == set(WRITE_CASES)
    assert len(names) == 8


@pytest.mark.parametrize("name", sorted(WRITE_CASES))
def test_write_endpoint_resolves_one_cached_session(name: str) -> None:
    """Every session dependency the handler's tree resolves is the cached one.

    Guards against a provider quietly growing an uncached ``get_db_session``
    dependency, which would hand the handler and its services two different
    sessions -- the commit below would be observing the wrong one.
    """
    for route in _routes_for(name):
        session_deps = [d for d in _walk(route.dependant) if d.call is get_db_session]

        assert session_deps, f"{name} resolves no session at all"
        assert all(d.use_cache for d in session_deps), (
            f"{name} has an uncached session dependency, so it would get a second session"
        )


@pytest.mark.parametrize("name", sorted(WRITE_CASES))
async def test_write_endpoint_commits_before_returning(name: str) -> None:
    """The handler commits its own transaction, and does so after auditing."""
    timeline = _timeline()

    await WRITE_CASES[name](timeline)

    assert timeline.calls.mock_calls == [call.audit(), call.commit()]


async def test_get_hr_ai_config_maps_service_view_through_narrow_response() -> None:
    """The root read is ``service.get_view()`` mapped through the same narrow
    response the write endpoints already return -- not a second, separately
    maintained view of the same state."""
    service = AsyncMock()
    service.get_view = AsyncMock(return_value=_VIEW)

    result = await get_hr_ai_config(_HR, service=service)

    assert result.provider_configured == _VIEW.configured
    assert result.data_policy_accepted == _VIEW.data_policy_accepted
    assert result.ai_automation_consent == _VIEW.ai_automation_consent


def test_read_only_endpoints_take_no_session() -> None:
    """The three read-only endpoints stay probe-only: no session, nothing to commit."""
    for name in ("get_hr_ai_config", "get_data_policy", "get_provider_status"):
        endpoint = getattr(hr_router_module, name)
        assert "session" not in inspect.signature(endpoint).parameters

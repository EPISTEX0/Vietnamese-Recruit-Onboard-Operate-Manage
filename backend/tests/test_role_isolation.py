"""Permission matrix for the three-role model (SYSTEM_ADMIN / HR / USER).

This module is the executable specification of ADR-0009's Strict Isolation
Policy. It does not hand-write one case per endpoint; it walks the real
``app.routes`` dependency graph and compares the guard actually wired into
every route against the intended access class declared below.

Three layers of proof:

1. ``test_every_route_has_a_known_guard`` -- no route may be wired to a guard
   this module has never heard of. A new module inventing its own
   ``require_*`` helper fails here until it is registered.
2. ``test_route_access_matrix`` -- every route's resolved access class must
   equal the intended one from :data:`ACCESS_RULES`. A new endpoint under an
   unlisted path fails here rather than silently inheriting a neighbour's
   guard. This is the layer that catches *intent-mapping* errors: a route
   gated by the wrong-but-valid role.
3. ``test_guard_role_behaviour`` / the HTTP smoke tests -- prove the guards
   themselves return the right user or raise 403 per role, so layer 2's
   static mapping translates into real allow/deny behaviour.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.main import app
from src.modules.attendance.api.router import _require_hr as attendance_require_hr
from src.modules.employee.api.dependencies import get_current_employee
from src.modules.gmail.api.outbound_router import require_hr as outbound_require_hr
from src.modules.gmail.api.router import require_hr as gmail_require_hr
from src.modules.identity.api.admin_router import require_hr, require_system_admin
from src.modules.identity.container import get_current_user
from src.modules.identity.domain.entities import User, UserRole
from src.modules.identity.domain.exceptions import AccessDeniedError
from src.modules.recruitment.api.candidate_router import require_hr as candidate_require_hr
from src.modules.recruitment.api.conflict_router import require_hr as conflict_require_hr
from src.modules.recruitment.api.evaluation_router import require_hr as evaluation_require_hr
from src.modules.recruitment.api.inbox_router import require_hr as inbox_require_hr
from src.modules.recruitment.api.job_application_router import (
    require_hr as job_application_require_hr,
)
from src.modules.recruitment.api.job_opening_router import require_hr as job_opening_require_hr


class Access(str, Enum):
    """The access class a route is intended to sit in."""

    PUBLIC = "public"
    """Unauthenticated. Login, password reset, first-run setup."""

    AUTHENTICATED = "authenticated"
    """Any signed-in user, no role gate. See ``UNGATED_ROUTES``."""

    EMPLOYEE = "employee"
    """Employee self-service: requires a linked, active Employee record."""

    HR = "hr"
    """HR business domain. SYSTEM_ADMIN and USER get 403."""

    SYSTEM_ADMIN = "system_admin"
    """System/infrastructure administration. HR and USER get 403."""


# ---------------------------------------------------------------------------
# Guard registry -- the complete set of role guards the application may use.
# ---------------------------------------------------------------------------

SYSTEM_ADMIN_GUARDS = {require_system_admin}

HR_GUARDS = {
    require_hr,
    attendance_require_hr,
    gmail_require_hr,
    outbound_require_hr,
    candidate_require_hr,
    conflict_require_hr,
    evaluation_require_hr,
    inbox_require_hr,
    job_application_require_hr,
    job_opening_require_hr,
}

# Self-service guards live per-module because each resolves a different
# service object; they all funnel through ``get_current_employee``.
EMPLOYEE_GUARD_NAMES = {"_require_active_employee"}

KNOWN_GUARDS = SYSTEM_ADMIN_GUARDS | HR_GUARDS | {get_current_employee, get_current_user}

# Anything whose name looks like a guard must be registered above.
_GUARD_NAME_RE = re.compile(r"^_?require_")


# ---------------------------------------------------------------------------
# Intended access matrix. Ordered; first matching rule wins.
# A trailing ``*`` matches a path prefix, otherwise the path must match exactly.
# ``method`` of ``*`` matches any HTTP method.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """One row of the intended permission matrix."""

    method: str
    path: str
    access: Access

    def matches(self, method: str, path: str) -> bool:
        """Return True when this rule governs the given route."""
        if self.method != "*" and self.method != method:
            return False
        if self.path.endswith("*"):
            return path.startswith(self.path[:-1])
        return path == self.path


ACCESS_RULES: tuple[Rule, ...] = (
    # --- Infrastructure / operational endpoints -----------------------------
    Rule("*", "/health", Access.PUBLIC),
    Rule("*", "/docs*", Access.PUBLIC),
    Rule("*", "/redoc", Access.PUBLIC),
    Rule("*", "/openapi.json", Access.PUBLIC),
    # --- System administration ---------------------------------------------
    # OAuth Client ID/Secret, LLM keys, allowed domains, audit log, user
    # management, runtime health. ADR-0009 section 2.
    Rule("*", "/api/system-admin/*", Access.SYSTEM_ADMIN),
    # --- Auth ---------------------------------------------------------------
    Rule("*", "/api/auth/login", Access.PUBLIC),
    Rule("*", "/api/auth/logout", Access.PUBLIC),
    Rule("*", "/api/auth/refresh", Access.PUBLIC),
    Rule("*", "/api/auth/setup", Access.PUBLIC),
    Rule("*", "/api/auth/setup-status", Access.PUBLIC),
    Rule("*", "/api/auth/forgot-password", Access.PUBLIC),
    Rule("*", "/api/auth/reset-password", Access.PUBLIC),
    Rule("*", "/api/auth/reset-password-token-info", Access.PUBLIC),
    Rule("*", "/api/auth/me", Access.AUTHENTICATED),
    Rule("*", "/api/auth/change-password", Access.AUTHENTICATED),
    Rule("*", "/api/auth/grant-status", Access.AUTHENTICATED),
    # The organization's shared Google Workspace account is an HR asset:
    # SYSTEM_ADMIN provisions the OAuth Client ID/Secret, HR connects the
    # business mailbox/calendar to it. ADR-0009 section 4.
    Rule("*", "/api/auth/organization-google-connection*", Access.HR),
    Rule("*", "/api/auth/callback", Access.HR),
    # --- HR namespace -------------------------------------------------------
    Rule("*", "/api/hr/*", Access.HR),
    # --- Employee self-service ---------------------------------------------
    Rule("*", "/api/ess/*", Access.EMPLOYEE),
    Rule("*", "/api/payslips/me*", Access.EMPLOYEE),
    Rule("*", "/api/employee-requests/me*", Access.EMPLOYEE),
    Rule("*", "/api/attendance/me/*", Access.EMPLOYEE),
    # --- Router-edge authenticated, authorization in the handler ------------
    # These serve both HR (full view) and employees (own record only), so the
    # role branch lives in the handler against the resolved Employee rather
    # than at the router edge. They are not gaps -- see the ownership checks
    # keyed on ``UserRole.HR`` in employee/api/router.py.
    Rule("GET", "/api/documents/{document_id}/download", Access.AUTHENTICATED),
    # --- Attendance (HR-facing) --------------------------------------------
    Rule("GET", "/api/attendance/settings/network", Access.AUTHENTICATED),
    Rule("*", "/api/attendance/*", Access.HR),
    # --- Employee records, org structure, documents -------------------------
    Rule("GET", "/api/employees", Access.AUTHENTICATED),
    Rule("GET", "/api/employees/{employee_id}", Access.AUTHENTICATED),
    Rule("PUT", "/api/employees/{employee_id}", Access.AUTHENTICATED),
    Rule("GET", "/api/employees/{employee_id}/documents", Access.AUTHENTICATED),
    Rule("POST", "/api/employees/{employee_id}/documents", Access.AUTHENTICATED),
    Rule("*", "/api/employees*", Access.HR),
    Rule("GET", "/api/departments", Access.AUTHENTICATED),
    Rule("*", "/api/departments*", Access.HR),
    Rule("GET", "/api/positions", Access.AUTHENTICATED),
    Rule("*", "/api/positions*", Access.HR),
    Rule("*", "/api/documents*", Access.HR),
    # --- Knowledge base, onboarding, HR assistant ---------------------------
    Rule("*", "/api/knowledge-base/*", Access.HR),
    # Task/employee-setup mutations are authorized inside OnboardingService
    # against the actor's role, not at the router edge.
    Rule("PATCH", "/api/onboarding/tasks/{task_id}", Access.AUTHENTICATED),
    Rule("PATCH", "/api/onboarding/processes/{process_id}/employee-setup", Access.AUTHENTICATED),
    Rule("*", "/api/onboarding/*", Access.HR),
    Rule("*", "/api/assistant/*", Access.HR),
    # --- Recruitment --------------------------------------------------------
    Rule("*", "/api/recruitment/candidates*", Access.HR),
    Rule("*", "/api/recruitment/job-openings*", Access.HR),
    Rule("*", "/api/recruitment/job-applications*", Access.HR),
    Rule("*", "/api/recruitment/inbox*", Access.HR),
    Rule("*", "/api/recruitment/evaluation*", Access.HR),
    Rule("*", "/api/recruitment/calendar-conflicts*", Access.HR),
    Rule("*", "/api/recruitment/cv-review*", Access.AUTHENTICATED),
    Rule("GET", "/api/recruitment/metrics", Access.AUTHENTICATED),
    # --- Gmail --------------------------------------------------------------
    Rule("*", "/api/gmail/*", Access.HR),
    Rule("*", "/api/outbound-emails*", Access.HR),
)


# ---------------------------------------------------------------------------
# Known role-gate gaps.
#
# These routes sit under an HR prefix but carry no role guard today: any
# signed-in user reaches them. They predate the three-role split and closing
# them is a separate, behaviour-changing decision. Registering them here keeps
# them countable and visible instead of invisible -- and any *new* ungated
# route under an HR prefix fails the matrix rather than joining them silently.
# ---------------------------------------------------------------------------

UNGATED_ROUTES: frozenset[str] = frozenset(
    {
        "GET /api/gmail/candidates/{candidate_id}/outbound",
        "POST /api/gmail/import/cancel",
        "POST /api/gmail/import/preview",
        "POST /api/gmail/import/start",
        "GET /api/gmail/import/status",
        "POST /api/gmail/outbound",
        "GET /api/gmail/outbound/{outbound_id}",
        "POST /api/gmail/outbound/{outbound_id}/retry",
        "POST /api/gmail/outbound/{outbound_id}/send",
        "POST /api/gmail/review/emails/{message_id}/classify-manually",
        "POST /api/gmail/send",
        "POST /api/gmail/sync",
        "GET /api/outbound-emails",
        "GET /api/outbound-emails/{outbound_id}",
        "GET /api/recruitment/candidates",
        "GET /api/recruitment/candidates/{candidate_id}",
        "GET /api/recruitment/candidates/{candidate_id}/cv/{document_id}",
        "POST /api/recruitment/candidates/{candidate_id}/accept",
        "POST /api/recruitment/candidates/{candidate_id}/archive",
        "POST /api/recruitment/candidates/{candidate_id}/reject",
        "POST /api/recruitment/candidates/{candidate_id}/reschedule-interview",
        "POST /api/recruitment/candidates/{candidate_id}/schedule-interview",
        "POST /api/recruitment/candidates/{candidate_id}/send-email",
        "GET /api/recruitment/job-openings",
        "GET /api/recruitment/job-openings/metrics",
        "GET /api/recruitment/job-openings/{job_opening_id}",
    }
)


# ---------------------------------------------------------------------------
# Route introspection
# ---------------------------------------------------------------------------


def _dependency_callables(dependant: Dependant) -> set[object]:
    """Collect every callable in a route's dependency tree."""
    found: set[object] = set()
    stack = [dependant]
    while stack:
        current = stack.pop()
        if current.call is not None:
            found.add(current.call)
        stack.extend(current.dependencies)
    return found


def _resolved_access(dependant: Dependant) -> Access:
    """Classify a route by the guards actually wired into it."""
    calls = _dependency_callables(dependant)
    if calls & SYSTEM_ADMIN_GUARDS:
        return Access.SYSTEM_ADMIN
    if calls & HR_GUARDS:
        return Access.HR
    if any(getattr(c, "__name__", "") in EMPLOYEE_GUARD_NAMES for c in calls):
        return Access.EMPLOYEE
    if get_current_user in calls:
        return Access.AUTHENTICATED
    return Access.PUBLIC


def _intended_access(method: str, path: str) -> Access | None:
    """Look up the intended access class, or None when unspecified."""
    for rule in ACCESS_RULES:
        if rule.matches(method, path):
            return rule.access
    return None


def _api_routes() -> list[tuple[str, str, Dependant]]:
    """Return (method, path, dependant) for every mounted API route."""
    routes: list[tuple[str, str, Dependant]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            routes.append((method, route.path, route.dependant))
    return routes


ALL_ROUTES = _api_routes()


def test_route_inventory_is_not_empty() -> None:
    """Guard against the matrix silently passing on an empty route list."""
    assert len(ALL_ROUTES) > 100, f"only {len(ALL_ROUTES)} routes discovered"


# ---------------------------------------------------------------------------
# Layer 1 -- no unregistered guards
# ---------------------------------------------------------------------------


def test_every_route_has_a_known_guard() -> None:
    """Every guard-shaped dependency must be in the registry above.

    A module that invents its own ``require_admin``/``require_hr`` helper and
    wires it into a route fails here until it is registered and classified.
    """
    unknown: dict[str, str] = {}
    for method, path, dependant in ALL_ROUTES:
        for call in _dependency_callables(dependant):
            name = getattr(call, "__name__", "")
            if not _GUARD_NAME_RE.match(name):
                continue
            if name in EMPLOYEE_GUARD_NAMES or call in KNOWN_GUARDS:
                continue
            qualified = f"{getattr(call, '__module__', '?')}.{name}"
            unknown[f"{method} {path}"] = qualified

    assert not unknown, (
        "Unregistered role guards found. Add them to SYSTEM_ADMIN_GUARDS / "
        f"HR_GUARDS and classify their routes:\n{unknown}"
    )


def test_legacy_admin_alias_is_gone() -> None:
    """``require_admin``/``AdminUserDep`` must not come back.

    The alias silently re-pointed 38 HR endpoints at SYSTEM_ADMIN because it
    kept unmigrated call sites compiling. Its absence is the invariant.
    """
    import src.modules.identity.api.admin_router as admin_router

    assert not hasattr(admin_router, "require_admin")
    assert not hasattr(admin_router, "AdminUserDep")


# ---------------------------------------------------------------------------
# Layer 2 -- the matrix itself
# ---------------------------------------------------------------------------


def test_every_route_is_classified() -> None:
    """No route may fall outside the intended matrix."""
    unclassified = [
        f"{method} {path}"
        for method, path, _ in ALL_ROUTES
        if _intended_access(method, path) is None
    ]
    assert not unclassified, (
        "Routes with no entry in ACCESS_RULES -- add a rule stating who may "
        f"reach them:\n{unclassified}"
    )


@pytest.mark.parametrize(
    ("method", "path", "dependant"),
    [pytest.param(m, p, d, id=f"{m} {p}") for m, p, d in ALL_ROUTES],
)
def test_route_access_matrix(method: str, path: str, dependant: Dependant) -> None:
    """Each route's wired guard must match its intended access class."""
    intended = _intended_access(method, path)
    assert intended is not None, f"{method} {path} is not in ACCESS_RULES"

    resolved = _resolved_access(dependant)
    key = f"{method} {path}"

    if key in UNGATED_ROUTES:
        assert resolved is Access.AUTHENTICATED, (
            f"{key} is registered in UNGATED_ROUTES but now resolves to "
            f"{resolved.value}. If it was intentionally gated, remove it from "
            "UNGATED_ROUTES."
        )
        return

    assert resolved is intended, f"{key}: intended {intended.value}, wired {resolved.value}"


def test_ungated_register_has_no_stale_entries() -> None:
    """Every registered gap must still correspond to a live route."""
    live = {f"{m} {p}" for m, p, _ in ALL_ROUTES}
    stale = sorted(UNGATED_ROUTES - live)
    assert not stale, f"UNGATED_ROUTES lists routes that no longer exist: {stale}"


def test_system_admin_surface_is_fully_gated() -> None:
    """No /api/system-admin route may be reachable below SYSTEM_ADMIN."""
    leaks = [
        f"{m} {p}"
        for m, p, d in ALL_ROUTES
        if p.startswith("/api/system-admin") and _resolved_access(d) is not Access.SYSTEM_ADMIN
    ]
    assert not leaks, f"system-admin routes not gated by require_system_admin: {leaks}"


def test_hr_namespace_is_fully_gated() -> None:
    """No /api/hr route may be reachable below HR."""
    leaks = [
        f"{m} {p}"
        for m, p, d in ALL_ROUTES
        if p.startswith("/api/hr/") and _resolved_access(d) is not Access.HR
    ]
    assert not leaks, f"/api/hr routes not gated by require_hr: {leaks}"


def test_no_hr_route_is_gated_by_system_admin() -> None:
    """The PR #281 regression: HR endpoints gated by SYSTEM_ADMIN.

    Every route whose intended class is HR must resolve to HR -- never to
    SYSTEM_ADMIN. This is the exact inversion the ``require_admin`` alias
    introduced across 38 endpoints.
    """
    inverted = [
        f"{m} {p}"
        for m, p, d in ALL_ROUTES
        if _intended_access(m, p) is Access.HR and _resolved_access(d) is Access.SYSTEM_ADMIN
    ]
    assert not inverted, f"HR business endpoints locked behind SYSTEM_ADMIN: {inverted}"


# ---------------------------------------------------------------------------
# Layer 3 -- the guards actually allow/deny per role
# ---------------------------------------------------------------------------


def _user(role: UserRole) -> User:
    """Build an in-memory user carrying the given role."""
    return User(
        id=uuid4(),
        email=f"{role.value}@example.com",
        name=role.value,
        role=role,
    )


ALL_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.HR, UserRole.USER)


async def _call_guard(guard, user: User) -> User:
    """Invoke a guard regardless of whether it is sync or async."""
    result = guard(user)
    if inspect.isawaitable(result):
        return await result
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", sorted(HR_GUARDS, key=lambda g: g.__module__))
@pytest.mark.parametrize("role", ALL_ROLES)
async def test_hr_guards_admit_only_hr(guard: object, role: UserRole) -> None:
    """Every HR guard admits HR and rejects the other two roles with 403."""
    user = _user(role)
    if role is UserRole.HR:
        assert await _call_guard(guard, user) is user
        return

    with pytest.raises((HTTPException, AccessDeniedError)) as exc_info:
        await _call_guard(guard, user)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ALL_ROLES)
async def test_system_admin_guard_admits_only_system_admin(role: UserRole) -> None:
    """``require_system_admin`` admits SYSTEM_ADMIN and 403s the rest."""
    user = _user(role)
    if role is UserRole.SYSTEM_ADMIN:
        assert await require_system_admin(user) is user
        return

    with pytest.raises(HTTPException) as exc_info:
        await require_system_admin(user)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "SYSTEM_ADMIN_ACCESS_DENIED"


# ---------------------------------------------------------------------------
# HTTP-level strict isolation, end to end through the ASGI stack.
# ---------------------------------------------------------------------------


@pytest.fixture
def client_as():
    """Yield a factory building a TestClient authenticated as a given role."""
    created: list[TestClient] = []

    def _build(role: UserRole) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: _user(role)
        test_client = TestClient(app)
        created.append(test_client)
        return test_client

    yield _build

    app.dependency_overrides.pop(get_current_user, None)
    for test_client in created:
        test_client.close()


def test_strict_isolation_system_admin_blocked_from_hr_api(client_as) -> None:
    """SYSTEM_ADMIN calling an HR endpoint gets 403 HR_ACCESS_DENIED."""
    response = client_as(UserRole.SYSTEM_ADMIN).get("/api/hr/employee-requests")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "HR_ACCESS_DENIED"


def test_strict_isolation_hr_blocked_from_system_admin_api(client_as) -> None:
    """HR calling a system-admin endpoint gets 403 SYSTEM_ADMIN_ACCESS_DENIED."""
    response = client_as(UserRole.HR).get("/api/system-admin/users")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "SYSTEM_ADMIN_ACCESS_DENIED"


def test_strict_isolation_plain_user_blocked_from_both(client_as) -> None:
    """A self-service USER reaches neither administrative namespace."""
    client = client_as(UserRole.USER)

    assert client.get("/api/hr/employee-requests").status_code == 403
    assert client.get("/api/system-admin/users").status_code == 403

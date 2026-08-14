"""The ESS assistant router registers every route the ESS client calls.

A route is only registered when its ``@router.post(...)`` decorator actually
runs at import time.  Indent a handler one level too far — under the function
above it, past that function's ``return`` — and the decorator never executes:
the module imports cleanly, ruff sees a nested function rather than dead code,
and the endpoint simply is not there.  The caller gets a 404 that looks like a
routing or proxy problem rather than a missing handler.

That is what happened to ``POST /api/ess/assistant/feedback``.  This test reads
the router's own route table, so it fails the moment a handler stops being
registered, whatever the reason.

One route the client calls is genuinely absent rather than mis-indented —
``/chat/stream`` — and it is recorded here as a strict xfail rather than left
out, so the file states the whole contract instead of only the part it passes.
"""

from __future__ import annotations

import os

os.environ.setdefault("AUTH_GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("AUTH_GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("AUTH_JWT_SECRET_KEY", "test-secret-key-32-chars-min-for-hs256")
os.environ.setdefault("AUTH_OAUTH_TOKEN_ENCRYPTION_KEY", "dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcw==")

import pytest  # noqa: E402

from src.modules.assistant.api.employee_router import (  # noqa: E402
    employee_assistant_router,
)

# Paths the frontend ESS assistant client (``frontend/lib/api/employee-assistant.ts``)
# posts to. Anything listed here must resolve to a handler.
_EXPECTED_POST_PATHS = [
    "/api/ess/assistant/chat",
    "/api/ess/assistant/feedback",
    "/api/ess/assistant/session/start",
    "/api/ess/assistant/session/end",
]

# The client posts here too, and the ESS router does not implement it — see
# ``test_known_missing_ess_route``. Kept as its own list rather than quietly
# dropped from the one above, so this file cannot report full coverage of the
# client's calls while the single route that breaks that claim goes unmentioned.
_MISSING_POST_PATHS = [
    "/api/ess/assistant/chat/stream",
]


def _registered_post_paths() -> set[str]:
    """Return the POST paths the router actually exposes."""
    return {
        route.path
        for route in employee_assistant_router.routes
        if "POST" in getattr(route, "methods", set())
    }


@pytest.mark.parametrize("path", _EXPECTED_POST_PATHS)
def test_ess_route_is_registered(path: str) -> None:
    """Each ESS endpoint the client calls is reachable on the router."""
    assert path in _registered_post_paths()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ESS /chat/stream is unimplemented: EmployeeAssistantService has no "
        "chat_stream, so this needs a streaming service method, not a route. "
        "AiChat sends every message through the stream path, so ESS chat 404s."
    ),
)
@pytest.mark.parametrize("path", _MISSING_POST_PATHS)
def test_known_missing_ess_route(path: str) -> None:
    """A route the client calls that the ESS router does not serve.

    Recorded as a strict xfail so the gap is visible in test output instead of
    being an absence nobody reads. ``strict=True`` means implementing the
    endpoint turns this into an XPASS failure, which is the prompt to move the
    path into ``_EXPECTED_POST_PATHS``.
    """
    assert path in _registered_post_paths()


def test_handlers_are_defined_at_module_level() -> None:
    """No ESS handler hides inside another function.

    The registration check above catches a nested handler only for paths this
    file already knows about.  This one is the general guard: every registered
    endpoint's ``__qualname__`` must be a bare function name, so a handler
    nested inside a sibling handler is caught even before anyone adds its path
    to the list.
    """
    nested = [
        route.endpoint.__qualname__
        for route in employee_assistant_router.routes
        if hasattr(route, "endpoint") and "." in route.endpoint.__qualname__
    ]
    assert nested == []

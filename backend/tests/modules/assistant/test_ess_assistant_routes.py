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

``/chat/stream`` was absent for a different reason — nothing implemented it —
and was recorded here as a strict xfail until it was.  It is now an ordinary
entry in the list below, which is the point of having written it down: the
client posts to it on every message, so its absence was a 404 on every employee
chat turn rather than a missing streaming nicety.  What the endpoint actually
streams is asserted in ``test_ess_chat_stream_integration.py``; this file only
holds the line that it exists.
"""

from __future__ import annotations

import os

os.environ.setdefault("AUTH_GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("AUTH_GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("AUTH_JWT_SECRET_KEY", "test-secret-key-32-chars-min-for-hs256")
os.environ.setdefault("AUTH_OAUTH_TOKEN_ENCRYPTION_KEY", "dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcw==")

import re  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from src.modules.assistant.api.employee_router import (  # noqa: E402
    employee_assistant_router,
)

# backend/tests/modules/assistant -> repo root -> the ESS client.
_ESS_CLIENT = (
    Path(__file__).resolve().parents[4] / "frontend" / "lib" / "api" / "employee-assistant.ts"
)

# Paths the frontend ESS assistant client (``frontend/lib/api/employee-assistant.ts``)
# posts to. Anything listed here must resolve to a handler.
_EXPECTED_POST_PATHS = [
    "/api/ess/assistant/chat",
    "/api/ess/assistant/chat/stream",
    "/api/ess/assistant/feedback",
    "/api/ess/assistant/session/start",
    "/api/ess/assistant/session/end",
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


def test_every_path_the_client_posts_to_is_covered_here() -> None:
    """The expected-path list matches what the client actually posts.

    ``test_ess_route_is_registered`` can only check paths somebody remembered
    to list, so it would keep passing if a sixth endpoint were added to the
    client and never to the router — the exact shape of the ``/chat/stream``
    gap.  This reads the client's own source instead, so the list cannot fall
    behind it silently.
    """
    client_source = _ESS_CLIENT.read_text(encoding="utf-8")

    # The client builds its URLs as `${BASE}/suffix`; BASE is the router prefix.
    posted = {
        f"/api/ess/assistant{match}"
        for match in re.findall(r"\$\{BASE\}(/[a-z/-]*)", client_source)
    }

    assert posted, f"found no ${{BASE}} calls in {_ESS_CLIENT} — has the client moved?"
    assert posted <= set(_EXPECTED_POST_PATHS), (
        f"the ESS client calls paths this file does not check: "
        f"{sorted(posted - set(_EXPECTED_POST_PATHS))}"
    )


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

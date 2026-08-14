"""Structural guard: the assistant module has exactly one session lookup.

The behavioural tests in ``test_session_ownership_integration.py`` pin down the
four handlers that exist *today*.  They cannot fail for a fifth handler written
next month — which is precisely how this bug arrived, as the same missing owner
filter copied across four places.

So this file constrains the shape of the code rather than its behaviour: only
``session_repository.py`` may turn a session id into a row, and the single
lookup it offers takes an owner.  A new handler that hand-rolls
``select(AssistantChatSession)`` fails here, at the moment it is written, with a
message pointing at the guard it should have used instead.

This is a lint, and lints go stale.  If the rule ever becomes the wrong rule,
change it deliberately — do not add a file to an exemption list to get green.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# backend/src/modules/assistant
ASSISTANT_ROOT = Path(__file__).resolve().parents[3] / "src" / "modules" / "assistant"

# The one module allowed to resolve a session id, because it is the module that
# makes the owner argument mandatory.
LOOKUP_OWNER = ASSISTANT_ROOT / "infrastructure" / "session_repository.py"

GUARD_HINT = (
    "Resolve sessions through ChatSessionGuard "
    "(src/modules/assistant/api/session_access.py) instead. "
    "Querying assistant_chat_sessions by id alone is the IDOR this guard exists to prevent."
)


def _python_files() -> list[Path]:
    """Every Python source file in the assistant module."""
    return sorted(ASSISTANT_ROOT.rglob("*.py"))


def _selects_chat_session(tree: ast.AST) -> bool:
    """True if the module calls ``select(AssistantChatSession)``."""
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "select"
        and any(isinstance(arg, ast.Name) and arg.id == "AssistantChatSession" for arg in node.args)
        for node in ast.walk(tree)
    )


def test_the_lookup_owner_exists() -> None:
    """Guard against this whole file silently passing if the module is renamed."""
    assert LOOKUP_OWNER.is_file(), f"{LOOKUP_OWNER} is missing — has the repository moved?"


def test_assistant_sources_were_found() -> None:
    """A bad root path would make every parametrised case vacuously pass."""
    assert len(_python_files()) > 5


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_only_the_repository_queries_chat_sessions(path: Path) -> None:
    """No module outside the repository may select a chat session."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    if not _selects_chat_session(tree):
        return

    assert path == LOOKUP_OWNER, (
        f"{path.relative_to(ASSISTANT_ROOT.parents[2])} queries AssistantChatSession directly. "
        f"{GUARD_HINT}"
    )


def test_repository_offers_no_lookup_without_an_owner() -> None:
    """Every public read on the repository takes an owner argument.

    The centralisation above is only worth anything if the one permitted
    lookup cannot be called without an owner. A ``get_by_id`` added here later
    would re-open the hole while every other test stayed green.
    """
    tree = ast.parse(LOOKUP_OWNER.read_text(encoding="utf-8"))

    repo = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "AssistantSessionRepository"
    )

    reads = [
        node
        for node in repo.body
        if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_")
    ]
    assert reads, "the repository exposes no public lookup at all"

    for method in reads:
        params = {arg.arg for arg in method.args.args}
        assert "owner_user_id" in params, (
            f"AssistantSessionRepository.{method.name}() can be called without an owner. "
            "Every public lookup must require one, or handlers can bypass the ownership rule "
            "without touching a single line of guard code."
        )

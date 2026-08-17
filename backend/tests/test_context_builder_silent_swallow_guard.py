"""Structural guard: every broad ``except`` in ``context_builder.py`` logs before it swallows.

## The property, not a count

Issue #375: nine handlers in ``context_builder.py`` -- one per context section
(org name, pipeline summary, job openings, onboarding, employee profile,
leave balance, pending requests, payslip summary, KB retrieval) -- caught
``except Exception`` and returned ``""`` with no log statement anywhere in
the handler body. The file imported no ``logging`` module at all. A real
Postgres outage there would degrade every context section to empty and leave
no trace that anything had failed.

The fix keeps the degrade-to-empty behaviour (#375's brief closes this
design question explicitly: an assistant should not 500 because one payroll
lookup failed) and only adds ``logger.exception(...)`` ahead of each
``return ""``. This file is the guard against the tenth handler being added
silently later.

The property this file enforces: **no ``except`` clause in
``context_builder.py`` that catches broadly (``Exception``, ``BaseException``,
or bare ``except:``) may have a body with no logging call in it.** "Logging
call" is any method call of the form ``<name>.<method>(...)`` where
``<name>`` is a bare identifier containing ``log`` (``logger.exception(...)``,
``log.warning(...)``) -- not narrowed to ``logger.exception`` specifically, so
a handler that logs at ``debug`` still counts as logged. The brief's own
opening census flagged that narrower matching as one of its blind spots; this
guard does not repeat it.

The receiver must be a bare name, not an attribute chain or a call result --
``self._logger.warning(...)`` and ``logging.getLogger(__name__).error(...)``
are deliberately not recognized. Every sibling ``application/`` module that
already logs (``tool_registry.py``, ``employee_tool_registry.py``,
``interview_scheduler_service.py``, ``streaming_loop.py``,
``employee_assistant_service.py``, ``assistant_service.py``) uses the same
module-level ``logger = logging.getLogger(__name__)`` convention this file
now also uses; matching only that shape, honestly, beats a chain-walking
matcher that nothing here exercises and that risks false-accepting an
unrelated call by coincidence of substring.

## Only this one file

#375's brief measured 60 silent broad-except handlers across ``src/`` and
explicitly scoped the ticket to the nine in ``context_builder.py`` -- "một
file, một hình thái". Widening this guard to all of ``src/`` would fail on
the other 51 immediately and turn a one-file fix into an unreviewed
mass-change; that is tracked as separate follow-up work, not silently
absorbed here.

## Guarding against a xanh-rỗng extractor (lesson from #359/#360/#370)

``test_the_census_reports_a_known_positive`` and
``test_the_census_stays_quiet_when_the_handler_logs`` run the visitor over
two synthetic modules that differ in exactly one way: one broad handler logs
before returning, the other doesn't. Without the second, a visitor whose
extractor matched everything (e.g. flagging every ``except`` regardless of
whether it logs) would leave ``test_no_silent_broad_except_in_context_builder``
green today by coincidence, then still be green the day someone deletes a
``logger.exception(...)`` call by accident -- the whole point of this guard.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# backend/
BACKEND_ROOT = Path(__file__).resolve().parent.parent
TARGET_PATH = BACKEND_ROOT / "src/modules/assistant/application/context_builder.py"

_BROAD_EXCEPTION_NAMES = frozenset({"Exception", "BaseException"})
_LOG_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)


@dataclass(frozen=True)
class Finding:
    """One broad except handler that swallows without logging."""

    path: Path
    lineno: int
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno}  {self.detail}"


def _is_broad_handler(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    if isinstance(handler.type, ast.Name):
        return handler.type.id in _BROAD_EXCEPTION_NAMES
    if isinstance(handler.type, ast.Tuple):
        return any(
            isinstance(elt, ast.Name) and elt.id in _BROAD_EXCEPTION_NAMES
            for elt in handler.type.elts
        )
    return False


def _handler_logs(handler: ast.ExceptHandler) -> bool:
    for stmt in handler.body:
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LOG_METHODS
                and isinstance(node.func.value, ast.Name)
                and "log" in node.func.value.id.lower()
            ):
                return True
    return False


class _SilentBroadExceptVisitor(ast.NodeVisitor):
    """Walk one module, reporting every broad except handler with no log call in its body."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.findings: list[Finding] = []

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if _is_broad_handler(node) and not _handler_logs(node):
            self.findings.append(
                Finding(
                    self._path,
                    node.lineno,
                    "broad except swallows without logging -- add a logger call "
                    "(e.g. logger.exception(...)) before the return",
                )
            )
        self.generic_visit(node)


def _broad_handlers(tree: ast.Module) -> list[ast.ExceptHandler]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and _is_broad_handler(node)
    ]


def _scan(trees: dict[Path, ast.Module]) -> list[Finding]:
    findings: list[Finding] = []
    for path, tree in trees.items():
        visitor = _SilentBroadExceptVisitor(path)
        visitor.visit(tree)
        findings.extend(visitor.findings)
    findings.sort(key=lambda f: (str(f.path), f.lineno))
    return findings


@lru_cache(maxsize=1)
def _target_tree() -> ast.Module:
    return ast.parse(TARGET_PATH.read_text(encoding="utf-8"), filename=str(TARGET_PATH))


def _synthetic(source: str) -> dict[Path, ast.Module]:
    return {TARGET_PATH: ast.parse(source)}


def test_the_scan_reaches_the_target_module() -> None:
    """A wrong path or a broken extractor would make the guard below vacuously true."""
    handlers = _broad_handlers(_target_tree())
    # Measured 9 broad except handlers in context_builder.py at the time of
    # writing (#375); 7 leaves headroom for ordinary growth while still
    # catching a path pointed at the wrong file or an extractor gone blind.
    assert len(handlers) > 7, f"only {len(handlers)} broad except handlers found in {TARGET_PATH}"


def test_no_silent_broad_except_in_context_builder() -> None:
    """No broad except handler in context_builder.py swallows without logging.

    Sanity floor: this must be 0, not the 9 measured before #375's fix --
    that number was the state *before* the fix, not a target to hold.
    """
    findings = _scan({TARGET_PATH: _target_tree()})
    assert not findings, (
        "Silent broad except (no log call) found in context_builder.py:\n"
        + "\n".join(str(f) for f in findings)
    )


_SILENT_MODULE = """
import logging

logger = logging.getLogger(__name__)


class ContextBuilder:
    async def _get_thing(self):
        try:
            return await self._repo.get_thing()
        except Exception:
            return ""
"""

_LOGGED_MODULE = """
import logging

logger = logging.getLogger(__name__)


class ContextBuilder:
    async def _get_thing(self):
        try:
            return await self._repo.get_thing()
        except Exception:
            logger.exception("Failed to load thing")
            return ""
"""


def test_the_census_reports_a_known_positive() -> None:
    """A broad except handler with no log call in its body must be caught.

    A visitor whose extractor silently stopped matching (wrong node type, an
    over-broad "counts as logging" check, ...) would leave the real guard
    green and empty; this is what would go red first, loudly, instead.
    """
    findings = _scan(_synthetic(_SILENT_MODULE))

    assert len(findings) == 1, findings
    assert findings[0].lineno == 11, findings


def test_the_census_stays_quiet_when_the_handler_logs() -> None:
    """A broad except handler that logs before returning must never be a finding.

    This is the other half of the known-positive pair: an extractor that
    flags every broad except regardless of whether it logs would pass the
    test above and still be useless -- it would also flag this module, which
    is exactly the fixed shape #375 leaves behind.
    """
    findings = _scan(_synthetic(_LOGGED_MODULE))
    assert not findings, findings

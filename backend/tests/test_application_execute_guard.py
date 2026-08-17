"""Structural guard: nothing under ``application/`` calls ``execute()`` on its own session.

## The property, not a count

Issue #370 (following #365, which closed the same violation in
``recruitment/application/interview_scheduler_service.py``): eighteen sites
across seven files in the ``application/`` layer ran ``self.session.execute(...)``
/ ``self._session.execute(...)`` directly -- raw SQL construction and dispatch
that belongs to a module's own ``infrastructure/`` repository, not to the
service orchestrating it. One of the eighteen (``context_builder.py:414``)
built its SQL with an f-string interpolating a table name and swallowed every
exception including real ones, not just "not found" -- the sharpest instance
of the same underlying problem, not a different one.

The property this file enforces: **no expression under an ``application/``
directory in ``src/`` calls ``.execute(...)`` on ``self.session`` or
``self._session``.** Reading through a repository constructed from that same
session (``SomeRepository(self._session).get_by_id(...)``) is fine and is
exactly the fix every one of the eighteen sites took -- the guard only
inspects the *direct* receiver of ``.execute``, so it never flags that shape.

## Why AST, not ``rg``

``execute(`` also matches ``tool_registry.execute(...)`` (the LLM tool
dispatch method) and ``ToolRegistry.execute`` calls in application code that
have nothing to do with a database session. A text search would either miss
the real violations behind an unusual receiver expression or drown them in
unrelated ``execute`` matches. An AST walk keyed on the receiver's attribute
chain (``self.session``/``self._session``) has neither blind spot.

## Only ``application/``, not all of ``src/``

``infrastructure/`` repositories are *supposed* to call
``self.session.execute(...)`` -- #365's own census counted 165 such call
sites there as the correct shape, the pattern every violation this file
guards against was refactored to reach. Scanning all of ``src/`` would flag
the very code the fix relocated the violations into.

## Guarding against a xanh-rỗng extractor (lesson from #359/#360)

``test_the_census_reports_a_known_positive`` and
``test_the_census_stays_quiet_on_clean_module`` run the visitor over
synthetic sources with a known violation and a known-clean module. Without
the first, a visitor whose extractor silently stopped matching (a typo'd
attribute name, the wrong AST node type) would leave
``test_no_self_session_execute_under_application`` green and empty --
indistinguishable from success. The clean module exercises the two patterns
that must **not** trip the guard: reading through a repository built from
``self._session``, and calling ``self._session.commit()``/``.rollback()``,
which the WORKSPACE_PROTOCOL "add()/flush()" rule explicitly reserves to the
application layer.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# backend/
BACKEND_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = BACKEND_ROOT / "src"

_TARGET_ATTRS = frozenset({"session", "_session"})

# (path relative to backend/, line number) -> why this one is not fixed here.
# Every entry must carry a reason; #359's brief (echoed in #370) requires it
# written next to the allowlist itself, not just in the ticket.
_ALLOWED: dict[tuple[str, int], str] = {}


@dataclass(frozen=True)
class Finding:
    """One reach-through: where it is, and what shape it took."""

    path: Path
    lineno: int
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno}  {self.detail}"


class _ExecuteOnOwnSessionVisitor(ast.NodeVisitor):
    """Walk one module, reporting every ``self.session.execute``/``self._session.execute`` call."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.findings: list[Finding] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "execute"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr in _TARGET_ATTRS
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self"
        ):
            self.findings.append(
                Finding(
                    self._path,
                    node.lineno,
                    f"calls .{func.value.attr}.execute(...) directly -- "
                    "route the query through that entity's own repository instead",
                )
            )
        self.generic_visit(node)


def _scan(
    trees: dict[Path, ast.Module], *, allowed: dict[tuple[str, int], str] | None = None
) -> list[Finding]:
    allowed = allowed or {}
    findings: list[Finding] = []
    for path, tree in trees.items():
        visitor = _ExecuteOnOwnSessionVisitor(path)
        visitor.visit(tree)
        for finding in visitor.findings:
            if (str(finding.path), finding.lineno) not in allowed:
                findings.append(finding)
    findings.sort(key=lambda f: (str(f.path), f.lineno))
    return findings


@lru_cache(maxsize=1)
def _application_trees() -> dict[Path, ast.Module]:
    """Parse every ``application/`` module once, keyed by path relative to ``backend/``."""
    trees: dict[Path, ast.Module] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if "application" not in path.relative_to(SRC_ROOT).parts:
            continue
        relative = path.relative_to(BACKEND_ROOT)
        trees[relative] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return trees


def _synthetic(sources: dict[str, str]) -> dict[Path, ast.Module]:
    return {Path(name): ast.parse(source) for name, source in sources.items()}


def test_the_scan_reaches_application_modules() -> None:
    """A wrong root path or filter would make the guard below vacuously true."""
    trees = _application_trees()
    # Measured 60+ application/ modules at the time of writing (#370); 40
    # leaves headroom for ordinary growth while still catching a root path
    # or filter pointed at the wrong place.
    assert len(trees) > 40, f"only {len(trees)} application/ modules found under {SRC_ROOT}"


def test_no_self_session_execute_under_application() -> None:
    """No application/ module calls .execute(...) on self.session/self._session directly.

    Sanity floor: this must be 0 outside ``_ALLOWED``, not the 18 measured
    before #370's fixes -- that number was the state *before* the fix, not a
    target to hold.
    """
    findings = _scan(_application_trees(), allowed=_ALLOWED)
    assert not findings, (
        "Raw self.session.execute()/self._session.execute() found in application/:\n"
        + "\n".join(str(f) for f in findings)
    )


_VIOLATION_MODULE = """
class Service:
    def __init__(self, session):
        self._session = session

    async def do(self):
        from sqlmodel import select
        statement = select(SomeEntity).where(SomeEntity.id == 1)
        result = await self._session.execute(statement)
        return result.scalars().first()
"""

_CLEAN_MODULE = """
class Service:
    def __init__(self, session, some_repo):
        self._session = session
        self._some_repo = some_repo

    async def do(self, entity_id):
        # Reading through the entity's own repository is the correct shape.
        entity = await self._some_repo.get_by_id(entity_id)
        # Application layer may still commit/rollback its own transaction.
        await self._session.commit()
        return entity
"""


def test_the_census_reports_a_known_positive() -> None:
    """A direct self._session.execute(...) call must be caught.

    A visitor whose extractor silently stopped matching (wrong node type, a
    typo'd attribute set, ...) would leave the real guard green and empty;
    this is what would go red first, loudly, instead.
    """
    findings = _scan(_synthetic({"src/pkg/application/service.py": _VIOLATION_MODULE}))

    assert len(findings) == 1, findings
    assert findings[0].lineno == 9, findings


def test_the_census_stays_quiet_on_repository_and_commit() -> None:
    """Reading via a repository and calling commit()/rollback() must never be findings."""
    findings = _scan(_synthetic({"src/pkg/application/service.py": _CLEAN_MODULE}))
    assert not findings, findings


def test_the_census_ignores_infrastructure() -> None:
    """infrastructure/ is not scanned, even though the same call shape there is correct."""
    trees = _application_trees()
    infra_paths = [p for p in trees if "infrastructure" in p.parts]
    assert not infra_paths, f"the application/ filter must never admit: {infra_paths}"

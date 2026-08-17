"""Structural guard: nothing in ``src/`` reads another object's ``session``/``_session``.

## The property, not a string

Issue #359: a service or router held a reference to some *other* object
(a repository, or in the ``candidate_router`` case another service) and
reached past its API straight at the session it carries, running
``add``/``flush``/``execute`` itself. Three of those crossed a private
attribute. This is not a live bug — every session in production traces back
to the same request-scoped ``get_db_session`` — but it is a coupling that
survives refactors invisibly, and one of the twelve sites (``auth_service.py``)
was kept alive by a test that mocked the reach-through itself
(``repository.session = MagicMock()``) rather than the repository's real API.

The property this file enforces: **no expression in ``src/`` reads the
``session`` or ``_session`` attribute of anything but ``self``.** That
covers the plain form (``other.session``, ``other._session``) and the
``getattr(other, "session")`` form the AST census used to size this ticket
(12 sites, not the 8 an ``rg``-by-name census had found) also had to catch.

## Why AST, not ``rg``

A grep guard here is exactly the failure mode #359 diagnoses in its own
predecessor: the ticket's own opening census used ``rg`` on the *repository*
side and missed four sites in ``candidate_router.py`` that reach into a
*service*'s private ``_session`` -- same property, different vocabulary. An
AST walk keyed on the attribute name, independent of which class owns it,
does not have that blind spot.

## Only ``src/``, not ``tests/``

``tests/`` uses ``harness.session`` as a legitimate fixture-composition
pattern in dozens of places -- scanning it would drown any real finding in
noise. ``tests/modules/recruitment/test_calendar_conflicts.py`` does hold six
``harness.service._session`` reach-throughs of the same shape this file
guards against in production code, but fixing test helpers is a separate,
smaller-blast-radius change; out of scope here, tracked separately rather
than silently absorbed into this census's reach.

## Guarding against a xanh-rỗng extractor (lesson from #360)

``test_the_census_reports_a_known_positive`` and
``test_the_census_stays_quiet_on_self_session`` run the visitor over
synthetic sources with a known violation and a known-clean module,
respectively. Without the first, a visitor whose extractor silently stopped
matching anything (regex typo'd, wrong node type, ...) would leave
``test_no_session_reach_through_in_src`` green and empty -- which reads
exactly like success. The known-positive module exercises all three shapes
(public attribute, private attribute, ``getattr``) in one pass so a partial
regression -- say, the ``getattr`` branch silently dropped -- still trips it.
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
# Every entry must carry a reason; #359's brief requires it written next to
# the allowlist itself, not just in the ticket.
_ALLOWED: dict[tuple[str, int], str] = {
    (
        "src/modules/gmail/application/email_sync_service.py",
        lineno,
    ): "Split into #361: needs the session threaded through the constructor "
    "instead, which changes two call sites (gmail/container.py, gmail/worker.py) "
    "-- a bigger, separate change from the in-place fixes in #359."
    for lineno in (181, 252, 539, 545)
}


@dataclass(frozen=True)
class Finding:
    """One reach-through: where it is, and what shape it took."""

    path: Path
    lineno: int
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno}  {self.detail}"


class _ReachThroughVisitor(ast.NodeVisitor):
    """Walk one module, reporting every read of ``.session``/``._session`` off a non-``self``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.findings: list[Finding] = []

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _TARGET_ATTRS:
            is_self = isinstance(node.value, ast.Name) and node.value.id == "self"
            if not is_self:
                self.findings.append(
                    Finding(
                        self._path,
                        node.lineno,
                        f"reads .{node.attr} of a non-self object -- "
                        "go through that object's own API instead",
                    )
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _TARGET_ATTRS
            and not (isinstance(node.args[0], ast.Name) and node.args[0].id == "self")
        ):
            self.findings.append(
                Finding(
                    self._path,
                    node.lineno,
                    f"getattr(..., {node.args[1].value!r}) reaches a session indirectly -- "
                    "a string literal is invisible to the attribute check above",
                )
            )
        self.generic_visit(node)


def _scan(
    trees: dict[Path, ast.Module], *, allowed: dict[tuple[str, int], str] | None = None
) -> list[Finding]:
    allowed = allowed or {}
    findings: list[Finding] = []
    for path, tree in trees.items():
        visitor = _ReachThroughVisitor(path)
        visitor.visit(tree)
        for finding in visitor.findings:
            if (str(finding.path), finding.lineno) not in allowed:
                findings.append(finding)
    findings.sort(key=lambda f: (str(f.path), f.lineno))
    return findings


@lru_cache(maxsize=1)
def _src_trees() -> dict[Path, ast.Module]:
    """Parse every ``src/`` module once, keyed by path relative to ``backend/``."""
    trees: dict[Path, ast.Module] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(BACKEND_ROOT)
        trees[relative] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return trees


def _synthetic(sources: dict[str, str]) -> dict[Path, ast.Module]:
    return {Path(name): ast.parse(source) for name, source in sources.items()}


def test_the_scan_reaches_src() -> None:
    """A wrong root path would make the guard below vacuously true."""
    trees = _src_trees()
    # Measured 269 at the time of writing; 250 leaves headroom for ordinary
    # growth while still catching a root path pointed at the wrong place.
    assert len(trees) > 250, f"only {len(trees)} source files found under {SRC_ROOT}"


def test_no_session_reach_through_in_src() -> None:
    """No code in src/ reads .session/._session off an object other than self.

    Sanity floor: this must be 0 outside ``_ALLOWED``, not the 12 measured
    before #359's fixes -- that number was the state *before* the fix, not a
    target to hold.
    """
    findings = _scan(_src_trees(), allowed=_ALLOWED)
    assert not findings, "Session reach-through found in src/:\n" + "\n".join(
        str(f) for f in findings
    )


_VIOLATION_MODULE = """
class Service:
    def __init__(self, repo):
        self._repo = repo

    async def do(self):
        await self._repo.session.execute("public attribute")
        await self._repo._session.flush()
        getattr(self._repo, "session").execute("indirect")
"""

_CLEAN_MODULE = """
class Service:
    def __init__(self, session):
        self._session = session

    async def do(self):
        await self._session.execute("x")
        await self._session.flush()
        return getattr(self, "_session")
"""


def test_the_census_reports_a_known_positive() -> None:
    """All three reach-through shapes -- public attr, private attr, getattr -- must be caught.

    A visitor whose extractor silently stopped matching (wrong node type, a
    typo'd attribute set, ...) would leave the real guard green and empty;
    this is what would go red first, loudly, instead.
    """
    findings = _scan(_synthetic({"src/pkg/service.py": _VIOLATION_MODULE}))

    assert len(findings) == 3, findings
    assert [f.lineno for f in findings] == [7, 8, 9], findings


def test_the_census_stays_quiet_on_self_session() -> None:
    """self._session and getattr(self, ...) are the correct pattern and must never be findings."""
    findings = _scan(_synthetic({"src/pkg/service.py": _CLEAN_MODULE}))
    assert not findings, findings

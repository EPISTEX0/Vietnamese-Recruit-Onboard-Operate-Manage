"""Structural guard: nobody calls a FastAPI provider without filling in its ``Depends`` slots.

``Depends(...)`` is a *marker*, not a value. FastAPI reads those markers while
resolving a request and passes real objects. Nothing else does. Call such a
function yourself and Python does the ordinary thing with whatever the parameter
declares.

A repository writes the marker in two places, and they fail differently:

* ``session=Depends(get_db_session)`` -- Python binds the
  ``fastapi.params.Depends`` instance itself. The call succeeds, the object is
  built, and it holds a sentinel where a session belongs, so the failure
  surfaces later, somewhere else, as an ``AttributeError`` on an object that
  looks fine in a debugger. Both known incidents are this form.
* ``session: Annotated[AsyncSession, Depends(get_db_session)]`` -- there is no
  default, so omitting it raises ``TypeError`` at the call. That is the loud
  failure, and it is caught here anyway: a census that registered only the
  silent form would report "clean" over two thirds of this repository's
  providers, which reads exactly like coverage.

The third shape is the annotated form *with* a default
(``service: DocumentServiceDep = None``, twelve of them in
``src/modules/knowledge_base/api/router.py``). It is silent like the first and
binds that literal instead.

This has now happened twice. #327: ``get_password_reset_service`` called
``await get_send_service()`` bare, and every password-reset email silently
failed to send for three months behind an anti-enumeration 200. #332: the
reclassify handler called ``get_classification_service(email_repo=..., audit_logger=...)``
and left ``session`` out, breaking the endpoint on every request.

The census #327 shipped with looked for *empty parentheses*, which is why it
could not see #332 at all. The defect is not "called with no arguments" — it is
"called with fewer arguments than it has ``Depends`` defaults". A bare call is
just the case where that count is zero.

What this file checks
---------------------

``test_every_depends_default_is_supplied_at_call_sites`` is the census. It
resolves each call through the calling module's own imports -- absolute,
relative, and plain ``import a.b.c`` alike -- so two modules that both define
``get_audit_service`` are told apart rather than shadowing each other by name.

The registry behind it reads ``Depends`` from defaults *and* from annotations,
including the ``SessionDep = Annotated[AsyncSession, Depends(get_db_session)]``
aliases this repository declares at module level and then uses as a bare name --
where the parameter's own annotation is an ``ast.Name`` with no ``Depends``
anywhere in it. Each of those capabilities has a self-test below that fails if
it stops seeing what it claims to see; without one, a registry that quietly
stopped resolving aliases would look identical to a clean repository.

``test_no_provider_is_reached_through_an_unanalysable_indirection`` is the
census defending its own blind spot. Static resolution follows names and
module attributes; it cannot follow a value. Rather than quietly missing
``f = get_send_service; await f()``, this rejects the indirection itself. There
are none today, so the rule costs nothing until someone writes the first one --
at which point the right answer is to call the builder directly, not to widen
this file.

``test_the_census_reports_a_planted_omission`` and its negative twin run the
analyser over synthetic sources. Without them a resolver that silently stopped
matching anything would leave the census green and empty, which reads exactly
like success.

Known limits, stated rather than implied
----------------------------------------

* Only module-level ``def``/``async def`` are registered. A ``Depends`` marker
  on a method or a nested function is not seen; none exist.
* Annotation aliases are read from module-level assignments only. One declared
  inside a function or a class body would not be recognised, and a provider
  annotated with it would drop out of the registry silently; there are none
  today (measured: zero ``Annotated[..., Depends(...)]`` assignments below
  module level).
* An alias reached as an attribute (``deps.SessionDep``) or written as a string
  (``session: "SessionDep"``) is not recognised either. Both are zero in this
  tree, and both would remove a provider from the registry rather than add a
  false one.
* Import bindings are collected flat over the whole module -- this codebase puts
  many container imports inside function bodies, so scope-accurate binding would
  reject more than it bought. Two function-local imports binding the same name
  to different modules would resolve to whichever comes last in the file.
* ``Depends`` and ``Annotated`` are both recognised by name (``Depends(...)``,
  ``x.Depends(...)``, ``Annotated[...]``, ``t.Annotated[...]``). Importing
  either under a different name would hide a provider from the registry.
* A call reached through anything but a name or a dotted module path -- an
  element of a list, a value returned by a factory -- is not resolved. The
  simple, writable forms of that (``alias = provider``, ``getattr``) are
  rejected outright above; the elaborate ones are neither caught nor reported.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# backend/
BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Every directory whose Python this repository ships or runs. ``src`` alone is
# where both known defects lived, but a worker under ``scripts/`` or a data
# migration under ``alembic/`` calls containers with exactly the same freedom
# and none of FastAPI's resolution, so they are scanned too.
SCANNED_ROOTS = ("src", "tests", "alembic", "scripts")

FIX_HINT = (
    "Extract a builder that takes the dependency explicitly -- see "
    "build_send_service / build_outbound_email_service in "
    "src/modules/gmail/container.py -- and call that instead. Supplying the "
    "argument at the call site works too, but leaves the next caller the same trap."
)


@dataclass(frozen=True)
class DependsParam:
    """One parameter FastAPI is meant to fill, and what a direct call binds instead."""

    name: str
    # Phrased for the finding message, because the three declaration forms fail
    # in three different ways and a single sentence would be wrong for two.
    on_omission: str


# ``session=Depends(get_db_session)``: the marker object becomes the argument.
_BINDS_MARKER = "binds the fastapi.params.Depends marker itself"
# ``session: Annotated[AsyncSession, Depends(get_db_session)]``: no default at all.
_NO_DEFAULT = "raises TypeError, since it has no default"


@dataclass(frozen=True)
class DependsFunction:
    """A module-level function that declares at least one ``Depends`` parameter."""

    module: Path
    name: str
    lineno: int
    depends_params: tuple[DependsParam, ...]
    # Parameter names in positional order, so a positional argument at index i
    # can be matched to the name it fills.
    positional_params: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.name} ({self.module}:{self.lineno})"


@dataclass(frozen=True)
class Finding:
    """One census result: where it is, and what is wrong with it."""

    module: Path
    lineno: int
    message: str

    def __str__(self) -> str:
        return f"{self.module}:{self.lineno}  {self.message}"


def _is_depends_call(node: ast.expr | None) -> bool:
    """True for ``Depends(...)`` and ``fastapi.Depends(...)``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "Depends"
    return isinstance(func, ast.Attribute) and func.attr == "Depends"


def _is_annotated_depends(annotation: ast.expr | None) -> bool:
    """True for ``Annotated[T, ..., Depends(...)]`` written out in full."""
    if not isinstance(annotation, ast.Subscript):
        return False
    base = annotation.value
    name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
    if name != "Annotated":
        return False
    parameters = annotation.slice
    elements = parameters.elts if isinstance(parameters, ast.Tuple) else [parameters]
    return any(_is_depends_call(element) for element in elements)


def _carries_depends(annotation: ast.expr | None, aliases: frozenset[str]) -> bool:
    """True if this annotation asks FastAPI for the value, spelled out or via an alias."""
    if _is_annotated_depends(annotation):
        return True
    return isinstance(annotation, ast.Name) and annotation.id in aliases


def _describe(
    function: ast.FunctionDef | ast.AsyncFunctionDef, aliases: frozenset[str]
) -> tuple[tuple[DependsParam, ...], tuple[str, ...]]:
    """Return ``(parameters FastAPI must fill, parameter names in positional order)``."""
    args = function.args
    positional = args.posonlyargs + args.args

    # ``defaults`` covers the *trailing* positional parameters, so pad the front.
    padded_defaults: list[ast.expr | None] = [
        *([None] * (len(positional) - len(args.defaults))),
        *args.defaults,
    ]
    pairs = [*zip(positional, padded_defaults), *zip(args.kwonlyargs, args.kw_defaults)]

    depends: list[DependsParam] = []
    for arg, default in pairs:
        # The default is checked first: when both carry a marker, the default is
        # what an omitted argument actually binds.
        if _is_depends_call(default):
            depends.append(DependsParam(arg.arg, _BINDS_MARKER))
        elif _carries_depends(arg.annotation, aliases):
            on_omission = (
                _NO_DEFAULT if default is None else f"binds its default, {ast.unparse(default)}"
            )
            depends.append(DependsParam(arg.arg, on_omission))

    return tuple(depends), tuple(arg.arg for arg in positional)


def _module_path(relative: Path) -> str:
    """``src/modules/gmail/container.py`` -> ``src.modules.gmail.container``."""
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _absolute_import_base(path: Path, node: ast.ImportFrom) -> str | None:
    """Resolve ``from . import x`` / ``from ..y import x`` to a dotted module name."""
    if node.level == 0:
        return node.module

    # ``.`` is the package the module lives in, for a plain module and for the
    # package's own ``__init__.py`` alike; each further dot goes up one.
    parts = list(path.parent.parts)
    ascend = node.level - 1
    if ascend > len(parts):
        return None
    if ascend:
        parts = parts[:-ascend]
    base = ".".join(parts)
    return f"{base}.{node.module}" if node.module else base


def _bindings(
    path: Path, tree: ast.Module, modules: dict[str, Path]
) -> tuple[dict[str, tuple[Path, str]], dict[str, Path]]:
    """Split this module's imports into ``local name -> (module, original)`` and ``-> module``.

    Module keys are whatever the source can write to reach the module: the bound
    name for ``import x.y as z`` and ``from x import y``, and the full dotted
    path for a plain ``import x.y``, which binds ``x`` but is used as
    ``x.y.thing``.
    """
    names: dict[str, tuple[Path, str]] = {}
    imported_modules: dict[str, Path] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = _absolute_import_base(path, node)
            if base is None:
                continue
            for alias in node.names:
                local = alias.asname or alias.name
                # ``from pkg.mod import thing`` and ``from pkg import mod`` are
                # the same syntax; only the filesystem tells them apart.
                submodule = modules.get(f"{base}.{alias.name}")
                if submodule is not None:
                    imported_modules[local] = submodule
                elif base in modules:
                    names[local] = (modules[base], alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in modules:
                    imported_modules[alias.asname or alias.name] = modules[alias.name]
    return names, imported_modules


def _assigned(node: ast.stmt) -> tuple[list[str], ast.expr | None]:
    """Return ``(names bound, value)`` for a module-level assignment, else ``([], None)``."""
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)], node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id], node.value
    return [], None


def _depends_aliases(
    trees: dict[Path, ast.Module], bindings: dict[Path, dict[str, tuple[Path, str]]]
) -> dict[Path, frozenset[str]]:
    """Per module, the names that stand for ``Annotated[T, Depends(...)]``.

    This repository declares those once per router (``SessionDep``, ``HRUserDep``,
    ``CurrentUserDep``) and then annotates parameters with the bare name, so the
    parameter's own annotation contains no ``Depends`` for a walk to find. Two
    further hops have to be followed or the alias goes cold: it may be imported
    from the module that declared it, and it may be re-bound to a second name.
    Both are resolved to a fixed point, since either can feed the other.
    """
    aliases: dict[Path, set[str]] = {}
    rebinds: dict[Path, list[tuple[str, str]]] = {}
    for path, tree in trees.items():
        for node in tree.body:
            targets, value = _assigned(node)
            if value is None:
                continue
            if _is_annotated_depends(value):
                aliases.setdefault(path, set()).update(targets)
            elif isinstance(value, ast.Name):
                rebinds.setdefault(path, []).extend((target, value.id) for target in targets)

    changed = True
    while changed:
        changed = False
        for path in trees:
            here = aliases.setdefault(path, set())
            for local, (source, original) in bindings.get(path, {}).items():
                if original in aliases.get(source, set()) and local not in here:
                    here.add(local)
                    changed = True
            for target, source_name in rebinds.get(path, []):
                if source_name in here and target not in here:
                    here.add(target)
                    changed = True

    return {path: frozenset(names) for path, names in aliases.items() if names}


def _build_registry(trees: dict[Path, ast.Module]) -> dict[Path, dict[str, DependsFunction]]:
    """Index every module-level function with a ``Depends`` parameter, by module."""
    modules = {_module_path(path): path for path in trees}
    bindings = {path: _bindings(path, tree, modules)[0] for path, tree in trees.items()}
    aliases = _depends_aliases(trees, bindings)

    registry: dict[Path, dict[str, DependsFunction]] = {}
    for path, tree in trees.items():
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            depends, positional = _describe(node, aliases.get(path, frozenset()))
            if depends:
                registry.setdefault(path, {})[node.name] = DependsFunction(
                    module=path,
                    name=node.name,
                    lineno=node.lineno,
                    depends_params=depends,
                    positional_params=positional,
                )
    return registry


class _ModuleCensus(ast.NodeVisitor):
    """Walk one module, reporting under-supplied provider calls and blind spots."""

    def __init__(
        self,
        path: Path,
        tree: ast.Module,
        registry: dict[Path, dict[str, DependsFunction]],
        modules: dict[str, Path],
    ) -> None:
        self._path = path
        self._registry = registry
        self._local = registry.get(path, {})
        self._known_names = {name for module in registry.values() for name in module}
        self._imported_functions, self._imported_modules = _bindings(path, tree, modules)
        self.findings: list[Finding] = []
        self.blind_spots: list[Finding] = []

    @staticmethod
    def _dotted(node: ast.expr) -> str | None:
        """Flatten ``a.b.c`` to ``"a.b.c"``; ``None`` if anything but names is involved."""
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        parts.append(current.id)
        return ".".join(reversed(parts))

    def _resolve(self, func: ast.expr) -> DependsFunction | None:
        """Return the registered function this call target names, if any."""
        if isinstance(func, ast.Name):
            if func.id in self._local:
                return self._local[func.id]
            binding = self._imported_functions.get(func.id)
            if binding is not None:
                module, original = binding
                return self._registry.get(module, {}).get(original)
            return None
        if isinstance(func, ast.Attribute):
            qualifier = self._dotted(func.value)
            module = self._imported_modules.get(qualifier) if qualifier else None
            if module is not None:
                return self._registry.get(module, {}).get(func.attr)
        return None

    def visit_Call(self, node: ast.Call) -> None:
        """Check one call.

        ``Depends(get_thing)`` is the correct usage and never a finding -- its
        argument is a reference, not a call -- but the walk still descends into
        it, because ``Depends(lambda: get_thing())`` hides a real call inside.
        """
        if _is_depends_call(node):
            self.generic_visit(node)
            return

        self._check_getattr(node)

        target = self._resolve(node.func)
        if target is not None:
            self._check_arguments(node, target)

        self.generic_visit(node)

    def _check_getattr(self, node: ast.Call) -> None:
        """Flag ``getattr(x, "get_thing")`` where the literal names a known provider."""
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2):
            return
        attribute = node.args[1]
        if isinstance(attribute, ast.Constant) and attribute.value in self._known_names:
            self.blind_spots.append(
                Finding(
                    self._path,
                    node.lineno,
                    f"getattr(..., {attribute.value!r}) reaches a provider by string, "
                    f"which no static check can follow. {FIX_HINT}",
                )
            )

    def _check_arguments(self, node: ast.Call, target: DependsFunction) -> None:
        """Report any ``Depends`` parameter this call leaves for FastAPI to fill."""
        if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
            keyword.arg is None for keyword in node.keywords
        ):
            self.blind_spots.append(
                Finding(
                    self._path,
                    node.lineno,
                    f"{target} is called with * / ** unpacking, so this check cannot tell "
                    f"which of its {len(target.depends_params)} Depends parameters are "
                    f"supplied. Pass them by name. {FIX_HINT}",
                )
            )
            return

        supplied = {keyword.arg for keyword in node.keywords}
        supplied.update(target.positional_params[: len(node.args)])

        missing = [param for param in target.depends_params if param.name not in supplied]
        if not missing:
            return

        # Spelled out per parameter: what an omission costs depends on how the
        # parameter was declared, and one blanket sentence would be false for
        # two of the three forms.
        detail = ", ".join(f"{param.name} ({param.on_omission})" for param in missing)
        self.findings.append(
            Finding(
                self._path,
                node.lineno,
                f"{target} is called without {detail} -- {len(missing)} of its "
                f"{len(target.depends_params)} Depends parameters. Only FastAPI fills those, "
                f"and it takes no part in a direct call. {FIX_HINT}",
            )
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        """Flag ``alias = get_thing`` -- from here on the census cannot follow the value."""
        self._check_alias(node.value, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Same as :meth:`visit_Assign`, for annotated assignments."""
        self._check_alias(node.value, node.lineno)
        self.generic_visit(node)

    def _check_alias(self, value: ast.expr | None, lineno: int) -> None:
        target = self._resolve(value) if isinstance(value, ast.Name) else None
        if target is not None:
            self.blind_spots.append(
                Finding(
                    self._path,
                    lineno,
                    f"{target} is aliased to a local name; a later call through that name "
                    f"is invisible to this census. {FIX_HINT}",
                )
            )


def run_census(trees: dict[Path, ast.Module]) -> tuple[list[Finding], list[Finding]]:
    """Analyse parsed modules, returning ``(under-supplied calls, blind spots)``.

    Keys are paths relative to the scan root; they are what module names are
    derived from, so the same function serves both the real tree and the
    synthetic sources the self-tests below feed it.
    """
    registry = _build_registry(trees)
    modules = {_module_path(path): path for path in trees}

    findings: list[Finding] = []
    blind_spots: list[Finding] = []
    for path, tree in trees.items():
        census = _ModuleCensus(path, tree, registry, modules)
        census.visit(tree)
        findings.extend(census.findings)
        blind_spots.extend(census.blind_spots)

    def in_file_order(finding: Finding) -> tuple[str, int]:
        return str(finding.module), finding.lineno

    return sorted(findings, key=in_file_order), sorted(blind_spots, key=in_file_order)


@lru_cache(maxsize=1)
def _repository_trees() -> tuple[tuple[Path, ast.Module], ...]:
    """Parse every scanned source file once, keyed by path relative to ``backend/``."""
    parsed: list[tuple[Path, ast.Module]] = []
    for root in SCANNED_ROOTS:
        for path in sorted((BACKEND_ROOT / root).rglob("*.py")):
            relative = path.relative_to(BACKEND_ROOT)
            parsed.append((relative, ast.parse(path.read_text(encoding="utf-8"))))
    return tuple(parsed)


def _synthetic(sources: dict[str, str]) -> dict[Path, ast.Module]:
    """Parse literal module sources for the self-tests."""
    return {Path(name): ast.parse(source) for name, source in sources.items()}


_PROVIDER_MODULE = """
from fastapi import Depends


async def get_db_session():
    yield None


async def get_thing(
    session=Depends(get_db_session),
    audit=Depends(get_db_session),
):
    return (session, audit)
"""

_ANNOTATED_MODULE = """
from typing import Annotated

from fastapi import Depends


async def get_db_session():
    yield None


SessionDep = Annotated[object, Depends(get_db_session)]


async def get_inline(session: Annotated[object, Depends(get_db_session)]):
    return session


async def get_aliased(session: SessionDep):
    return session
"""


def test_the_scan_reaches_the_containers() -> None:
    """A wrong root path would make every assertion below vacuously true."""
    trees = dict(_repository_trees())
    registry = _build_registry(trees)

    assert len(trees) > 500, f"only {len(trees)} source files found under {BACKEND_ROOT}"

    gmail_container = Path("src/modules/gmail/container.py")
    assert gmail_container in registry, (
        f"{gmail_container} declares no functions with Depends defaults -- "
        "the registry is not seeing FastAPI providers any more"
    )

    # Two real providers whose ``Depends`` is in an annotation rather than a
    # default. They are named here, and not only in the synthetic self-tests,
    # because the question that matters is whether the registry sees the way
    # *this* repository writes dependencies -- and it writes them this way far
    # more often than the other way.
    inline = Path("src/modules/recruitment/api/conflict_router.py")
    assert "require_hr" in registry.get(inline, {}), (
        f"{inline}:require_hr declares Annotated[User, Depends(get_current_user)] inline "
        "and is no longer registered"
    )
    aliased = Path("src/modules/recruitment/api/job_opening_router.py")
    assert "get_job_opening_service" in registry.get(aliased, {}), (
        f"{aliased}:get_job_opening_service is annotated only with the module-level "
        "SessionDep / CurrentUserDep aliases and is no longer registered"
    )

    # Measured 296 at the time of writing, of which 110 are annotation-form and
    # were invisible to the first version of this file.
    assert sum(len(module) for module in registry.values()) > 250


def test_every_depends_default_is_supplied_at_call_sites() -> None:
    """No call anywhere leaves a ``Depends`` parameter for a resolver that will not run."""
    findings, _ = run_census(dict(_repository_trees()))

    assert not findings, "Providers called without their dependencies:\n" + "\n".join(
        f"  {finding}" for finding in findings
    )


def test_no_provider_is_reached_through_an_unanalysable_indirection() -> None:
    """No alias, ``getattr``, or argument unpacking hides a provider call from the census."""
    _, blind_spots = run_census(dict(_repository_trees()))

    assert not blind_spots, "Provider calls this census cannot verify:\n" + "\n".join(
        f"  {spot}" for spot in blind_spots
    )


def test_the_census_reports_a_planted_omission() -> None:
    """The exact shape of #332: some arguments given, one ``Depends`` parameter left out."""
    findings, blind_spots = run_census(
        _synthetic(
            {
                "src/pkg/container.py": _PROVIDER_MODULE,
                "src/pkg/router.py": """
from src.pkg.container import get_thing


async def handler(audit):
    return await get_thing(audit=audit)
""",
            }
        )
    )

    assert not blind_spots
    assert len(findings) == 1, findings
    assert "get_thing" in findings[0].message
    assert "without session" in findings[0].message


def test_the_census_stays_quiet_on_correct_usage() -> None:
    """Full calls, ``Depends(provider)`` references, and same-named methods are not findings."""
    findings, blind_spots = run_census(
        _synthetic(
            {
                "src/pkg/container.py": _PROVIDER_MODULE,
                "src/pkg/router.py": """
from typing import Annotated

from fastapi import Depends

from src.pkg import container
from src.pkg.container import get_db_session, get_thing


class Client:
    async def get_thing(self):
        return None


async def handler(
    thing: Annotated[object, Depends(get_thing)],
    client: Client,
    session=Depends(get_db_session),
):
    await get_thing(session=session, audit=None)
    await container.get_thing(session, None)
    await client.get_thing()
""",
            }
        )
    )

    assert not findings, findings
    assert not blind_spots, blind_spots


def test_the_census_follows_relative_and_dotted_imports() -> None:
    """The import form must not decide whether a defect is visible.

    Each of these three call sites has the same defect; only the syntax used to
    reach the provider differs. An earlier revision saw the first and missed the
    other two, which is the failure mode that matters most in a guard -- the
    green run looks identical either way.
    """
    findings, blind_spots = run_census(
        _synthetic(
            {
                "src/pkg/__init__.py": "",
                "src/pkg/container.py": _PROVIDER_MODULE,
                "src/pkg/absolute.py": """
from src.pkg.container import get_thing


async def handler():
    return await get_thing(audit=None)
""",
                "src/pkg/relative.py": """
from .container import get_thing


async def handler():
    return await get_thing(audit=None)
""",
                "src/pkg/dotted.py": """
import src.pkg.container


async def handler():
    return await src.pkg.container.get_thing(audit=None)
""",
            }
        )
    )

    assert not blind_spots
    assert [finding.module.name for finding in findings] == [
        "absolute.py",
        "dotted.py",
        "relative.py",
    ], findings


def test_the_census_looks_inside_a_depends_argument() -> None:
    """``Depends(...)`` is correct usage, but it is not a place calls may hide."""
    findings, _ = run_census(
        _synthetic(
            {
                "src/pkg/container.py": _PROVIDER_MODULE,
                "src/pkg/router.py": """
from fastapi import Depends

from src.pkg.container import get_thing


async def handler(thing=Depends(lambda: get_thing(audit=None))):
    return thing
""",
            }
        )
    )

    assert len(findings) == 1, findings
    assert "without session" in findings[0].message


def test_the_census_registers_an_inline_annotated_dependency() -> None:
    """``Annotated[T, Depends(f)]`` written out in the signature is a Depends parameter."""
    findings, blind_spots = run_census(
        _synthetic(
            {
                "src/pkg/container.py": _ANNOTATED_MODULE,
                "src/pkg/router.py": """
from src.pkg.container import get_inline


async def handler():
    return await get_inline()
""",
            }
        )
    )

    assert not blind_spots
    assert len(findings) == 1, findings
    assert "without session" in findings[0].message


def test_the_census_registers_a_module_level_annotation_alias() -> None:
    """``session: SessionDep`` has no ``Depends`` in it; the alias has to be followed.

    This is the form the repository uses most, so a registry that missed it
    would be quiet about roughly two thirds of the providers here while looking
    exactly as green as one that covered them.
    """
    findings, blind_spots = run_census(
        _synthetic(
            {
                "src/pkg/container.py": _ANNOTATED_MODULE,
                "src/pkg/router.py": """
from src.pkg.container import get_aliased


async def handler():
    return await get_aliased()
""",
            }
        )
    )

    assert not blind_spots
    assert len(findings) == 1, findings
    assert "without session" in findings[0].message


def test_the_census_follows_an_alias_imported_from_another_module() -> None:
    """A provider annotated with an alias defined in a *different* module still registers."""
    findings, blind_spots = run_census(
        _synthetic(
            {
                "src/pkg/deps.py": _ANNOTATED_MODULE,
                "src/pkg/container.py": """
from src.pkg.deps import SessionDep


async def get_service(session: SessionDep):
    return session
""",
                "src/pkg/router.py": """
from src.pkg.container import get_service


async def handler():
    return await get_service()
""",
            }
        )
    )

    assert not blind_spots
    assert len(findings) == 1, findings
    assert "get_service" in findings[0].message


def test_the_census_follows_an_alias_of_an_alias() -> None:
    """``DbDep = SessionDep`` keeps the dependency; the census keeps it too."""
    findings, blind_spots = run_census(
        _synthetic(
            {
                "src/pkg/deps.py": _ANNOTATED_MODULE,
                "src/pkg/container.py": """
from src.pkg.deps import SessionDep

DbDep = SessionDep


async def get_service(session: DbDep):
    return session
""",
                "src/pkg/router.py": """
from src.pkg.container import get_service


async def handler():
    return await get_service()
""",
            }
        )
    )

    assert not blind_spots
    assert len(findings) == 1, findings
    assert "get_service" in findings[0].message


def test_a_finding_names_what_each_omitted_parameter_actually_binds() -> None:
    """The three declaration forms fail three different ways, and the message says which.

    Reporting a ``TypeError`` as a silently bound sentinel would send the reader
    looking for a corrupted object that does not exist, and the reverse would
    have them expect a crash that never comes.
    """
    findings, _ = run_census(
        _synthetic(
            {
                "src/pkg/container.py": """
from typing import Annotated

from fastapi import Depends


async def get_db_session():
    yield None


ServiceDep = Annotated[object, Depends(get_db_session)]


async def get_thing(
    marker=Depends(get_db_session),
    *,
    required: Annotated[object, Depends(get_db_session)],
    defaulted: ServiceDep = None,
):
    return (marker, required, defaulted)
""",
                "src/pkg/router.py": """
from src.pkg.container import get_thing


async def handler():
    return await get_thing()
""",
            }
        )
    )

    assert len(findings) == 1, findings
    message = findings[0].message
    assert "marker (binds the fastapi.params.Depends marker itself)" in message
    assert "required (raises TypeError, since it has no default)" in message
    assert "defaulted (binds its default, None)" in message
    assert "3 of its 3 Depends parameters" in message


def test_a_supplied_annotated_argument_does_not_excuse_a_missing_default() -> None:
    """Signatures that mix the two forms are the ones a partial call slips through.

    Eighty-three functions here declare both. A caller that fills the annotated
    parameters gets no error at all -- Python is satisfied -- while the
    ``Depends`` default quietly becomes a marker object. That is #332 exactly.
    """
    findings, blind_spots = run_census(
        _synthetic(
            {
                "src/pkg/container.py": """
from typing import Annotated

from fastapi import Depends


async def get_db_session():
    yield None


async def get_thing(
    session: Annotated[object, Depends(get_db_session)],
    audit=Depends(get_db_session),
):
    return (session, audit)
""",
                "src/pkg/router.py": """
from src.pkg.container import get_thing


async def handler(session):
    return await get_thing(session)
""",
            }
        )
    )

    assert not blind_spots
    assert len(findings) == 1, findings
    assert "without audit" in findings[0].message
    assert "1 of its 2 Depends parameters" in findings[0].message


def test_the_census_refuses_to_be_blinded_by_an_alias() -> None:
    """Rebinding a provider to a local name is reported rather than silently missed."""
    findings, blind_spots = run_census(
        _synthetic(
            {
                "src/pkg/container.py": _PROVIDER_MODULE,
                "src/pkg/router.py": """
from src.pkg.container import get_thing


async def handler():
    build = get_thing
    return await build()
""",
            }
        )
    )

    assert not findings
    assert len(blind_spots) == 1, blind_spots
    assert "aliased" in blind_spots[0].message

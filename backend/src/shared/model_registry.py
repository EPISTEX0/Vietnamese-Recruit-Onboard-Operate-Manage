"""Discovery of the SQLModel table classes that make up the database schema.

``alembic/env.py`` needs every table-mapped class to be imported before it
reads ``SQLModel.metadata``: a class that was never imported never registered
itself, so autogenerate sees a table that exists in the database but not in the
metadata and concludes it was deleted — emitting ``op.drop_table()``.

That import list used to be written by hand, and it fell behind: two modules
holding live tables were missing from it. Discovery here is derived from the
source tree instead, so adding a module cannot silently skip this step.

Detection is done with :mod:`ast` rather than by importing candidate modules,
because the modules that matter most are precisely the ones nothing has
imported yet.
"""

from __future__ import annotations

import ast
import importlib
from functools import lru_cache
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent
"""Filesystem root of the ``src`` package."""


def _declares_sqlmodel_table(node: ast.ClassDef) -> bool:
    """Whether ``node`` is a ``class X(SQLModel, table=True)`` declaration."""
    return any(
        kw.arg == "table" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in node.keywords
    )


def _declared_tablename(node: ast.ClassDef) -> str | None:
    """The literal ``__tablename__`` a class assigns, if it assigns one."""
    for stmt in node.body:
        targets = (
            stmt.targets
            if isinstance(stmt, ast.Assign)
            else [stmt.target]
            if isinstance(stmt, ast.AnnAssign)
            else []
        )
        for target in targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "__tablename__"
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                return stmt.value.value
    return None


def _module_name(path: Path) -> str:
    relative = path.relative_to(SRC_ROOT).with_suffix("")
    parts = relative.parts
    # ``pkg/__init__.py`` is the module ``src.pkg``. Importing it as
    # ``src.pkg.__init__`` would give the same file two entries in
    # ``sys.modules`` and re-execute its class bodies, which SQLAlchemy rejects
    # with "Table … is already defined for this MetaData instance".
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("src", *parts))


@lru_cache(maxsize=1)
def discover_entity_modules() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Find every module under ``src`` that declares SQLModel tables.

    Returns:
        Pairs of ``(dotted module path, table class names)``, sorted by module
        path. Modules declaring no tables are omitted.
    """
    found: list[tuple[str, tuple[str, ...]]] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        classes = tuple(node.name for node in _table_classes(path))
        if classes:
            found.append((_module_name(path), classes))
    return tuple(found)


@lru_cache(maxsize=1)
def discover_table_names() -> frozenset[str]:
    """Every ``__tablename__`` literal declared by a table class under ``src``.

    Read straight out of the source text, so it stays an independent statement
    of what the schema should contain rather than a restatement of whatever
    :func:`import_all_entity_modules` managed to import.
    """
    names: set[str] = set()
    for path in SRC_ROOT.rglob("*.py"):
        for node in _table_classes(path):
            tablename = _declared_tablename(node)
            if tablename is not None:
                names.add(tablename)
    return frozenset(names)


def _table_classes(path: Path) -> list[ast.ClassDef]:
    """Every table-mapped class declared in ``path``, nested ones included."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover - a broken source file fails elsewhere
        return []
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and _declares_sqlmodel_table(node)
    ]


def import_all_entity_modules() -> tuple[str, ...]:
    """Import every module declaring a SQLModel table, registering its metadata.

    Returns:
        The dotted module paths that were imported, sorted.
    """
    modules = tuple(module for module, _ in discover_entity_modules())
    for module in modules:
        importlib.import_module(module)
    return modules

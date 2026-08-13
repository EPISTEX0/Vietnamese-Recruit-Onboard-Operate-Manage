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


def _declares_sqlmodel_table(node: ast.stmt) -> bool:
    """Whether ``node`` is a ``class X(SQLModel, table=True)`` declaration."""
    if not isinstance(node, ast.ClassDef):
        return False
    return any(
        kw.arg == "table" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in node.keywords
    )


def _module_name(path: Path) -> str:
    relative = path.relative_to(SRC_ROOT).with_suffix("")
    return ".".join(("src", *relative.parts))


@lru_cache(maxsize=1)
def discover_entity_modules() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Find every module under ``src`` that declares SQLModel tables.

    Returns:
        Pairs of ``(dotted module path, table class names)``, sorted by module
        path. Modules declaring no tables are omitted.
    """
    found: list[tuple[str, tuple[str, ...]]] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover - a broken source file fails elsewhere
            continue
        classes = tuple(
            node.name  # type: ignore[union-attr]
            for node in tree.body
            if _declares_sqlmodel_table(node)
        )
        if classes:
            found.append((_module_name(path), classes))
    return tuple(found)


def import_all_entity_modules() -> tuple[str, ...]:
    """Import every module declaring a SQLModel table, registering its metadata.

    Returns:
        The dotted module paths that were imported, sorted.
    """
    modules = tuple(module for module, _ in discover_entity_modules())
    for module in modules:
        importlib.import_module(module)
    return modules

"""Measure model-vs-database drift the way ``alembic revision --autogenerate`` sees it.

Run as a script against an already-migrated database::

    python -m tests.schema_drift_probe postgresql+psycopg2://user:pw@host:5432/db

and it prints one JSON object on stdout. ``tests/test_schema_drift_ceiling.py``
is the caller; nothing imports this module.

**Why a separate process.** ``SQLModel.metadata`` is process-global. Inside a
pytest run it has already absorbed whatever the rest of the suite imported, and
any fixture that registers a throwaway table registers it there too -- the
comparison would then report tables the repository does not actually declare.
A fresh interpreter sees exactly what ``alembic`` sees on a cold start, which is
the only reading that predicts what autogenerate would emit.
``tests/test_alembic_metadata_complete.py`` isolates itself for the same reason.

**Why exec ``env.py`` instead of importing the registry directly.** Two options
have to match for the count to mean anything: the metadata, and
``include_object``. ``env.py`` owns both -- it calls
``import_all_entity_modules()`` and excludes ``UNMANAGED_TABLES`` from the
comparison. Reading them out of ``env.py`` keeps this probe in step if either
changes; re-deriving them here would let the two disagree silently.

That only covers the two options ``env.py`` passes *today*. A third one added
later -- ``compare_server_default``, say -- would change what autogenerate
reports while this probe kept comparing on the old terms, which reads as "no new
drift". ``_online_configure_keywords`` exists to make that a loud failure
instead: it reads the real ``context.configure`` call and refuses to run if the
keyword set has grown. See ``docs/schema-drift-audit.md`` section 8.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

# backend/ -- the directory holding alembic.ini and the alembic/ package.
BACKEND_DIR = Path(__file__).resolve().parents[1]


# What ``do_run_migrations`` in ``alembic/env.py`` passes to
# ``context.configure``. ``connection`` is per-run plumbing; the other two are
# comparison semantics this probe reproduces by hand. Anything else appearing
# there -- ``compare_server_default``, ``compare_type``, a naming convention --
# changes what autogenerate reports and would leave this probe under-reporting
# against a baseline that still looks accepted. Hence the check rather than a
# comment asking the next person to remember.
EXPECTED_CONFIGURE_KEYWORDS = {"connection", "target_metadata", "include_object"}


def _online_configure_keywords(source: str) -> set[str]:
    """Return the keyword names ``env.py`` passes to ``context.configure`` when online."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "do_run_migrations":
            continue
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "configure"
            ):
                return {kw.arg for kw in call.keywords if kw.arg}
    raise AssertionError(
        "could not find the context.configure() call in do_run_migrations() in "
        "alembic/env.py -- this probe reads it to prove it compares on the same "
        "terms autogenerate does"
    )


def _load_alembic_env() -> tuple[Any, Any]:
    """Return ``(SQLModel, include_object)`` exactly as ``alembic/env.py`` builds them.

    Executes the slice of ``env.py`` between the SQLModel import and
    ``target_metadata``, which is the part that populates the metadata and
    defines the comparison filter, without running any migration machinery.
    """
    source = (BACKEND_DIR / "alembic" / "env.py").read_text(encoding="utf-8")

    unexpected = _online_configure_keywords(source) - EXPECTED_CONFIGURE_KEYWORDS
    assert not unexpected, (
        f"alembic/env.py now passes {sorted(unexpected)} to context.configure(). "
        "Those options change what autogenerate compares, so this probe must pass "
        "them to MigrationContext.configure() too -- otherwise it silently reports "
        "less drift than a real autogenerate run would."
    )

    start = source.index("from sqlmodel import SQLModel")
    end = source.index("target_metadata = SQLModel.metadata")
    namespace: dict[str, Any] = {}
    exec(compile(source[start:end], "alembic/env.py (imports)", "exec"), namespace)
    assert "include_object" in namespace, (
        "alembic/env.py defines include_object below `target_metadata = ...`, so "
        "the executed slice no longer picks it up. Widen the slice; comparing "
        "without it counts gmail_label_mappings as drift."
    )
    return namespace["SQLModel"], namespace["include_object"]


def _column_names(obj: Any) -> str:
    """Render an index's or constraint's columns as ``(a, b)``."""
    try:
        return "(" + ", ".join(c.name for c in obj.columns) + ")"
    except Exception:  # noqa: BLE001 - a reflected object may expose no usable columns
        return "(?)"


def _identify(diff: tuple[Any, ...]) -> tuple[str, str, str]:
    """Reduce one ``compare_metadata`` entry to ``(operation, table, object)``.

    The triple is the *fingerprint*: stable across runs, and stable across
    unrelated edits to the same table, so a baseline written today still matches
    tomorrow. Everything volatile -- reflected type objects, ``repr`` of a
    ``Column``, dict ordering -- is deliberately left out of it and carried in
    the human-readable detail instead.
    """
    op = diff[0]

    if op in ("add_table", "remove_table"):
        return op, diff[1].name, "-"
    if op in ("add_table_comment", "remove_table_comment"):
        return op, diff[1].name, "-"
    if op in ("add_column", "remove_column"):
        return op, diff[2], diff[3].name
    if op in ("add_index", "remove_index"):
        index = diff[1]
        return op, getattr(index.table, "name", "?"), index.name or "unnamed"
    if op in ("add_constraint", "remove_constraint", "add_fk", "remove_fk"):
        constraint = diff[1]
        table = getattr(getattr(constraint, "table", None), "name", "?")
        return op, table, constraint.name or f"unnamed{_column_names(constraint)}"
    if op == "modify_comment":
        # ("modify_comment", schema, table, column, {existing…}, old, new). The
        # only modify_* left out of the value comparison below: a comment cannot
        # truncate or invalidate anything, and its text is free-form prose that
        # would churn the baseline on every wording tweak.
        return op, diff[2], diff[3]
    if op.startswith("modify_"):
        # ("modify_<attr>", schema, table, column, {existing…}, old, new).
        #
        # The old and new values belong in the *fingerprint*, not just the
        # detail. Keyed on the column alone, `users.avatar_url` is already an
        # accepted `TEXT -> VARCHAR`, so narrowing the model to
        # `max_length=10` -- an ALTER that truncates live data -- would land on
        # the same fingerprint and pass. Which way a column is moving is the
        # whole question for a type or nullability change.
        return op, diff[2], f"{diff[3]} {diff[5]} -> {diff[6]}"

    # Unknown operation: still produce *a* fingerprint rather than crashing, so
    # a new alembic version adding a diff kind surfaces as a readable failure.
    return op, "?", str(diff[1])[:80]


def _describe(diff: tuple[Any, ...]) -> str:
    """Say what the diff actually is, in one line, for a human reading a failure."""
    op = diff[0]

    if op in ("add_table", "remove_table"):
        return f"{len(diff[1].columns)} columns"
    if op in ("add_table_comment", "remove_table_comment"):
        return "table comment"
    if op in ("add_column", "remove_column"):
        column = diff[3]
        return f"{column.type} nullable={column.nullable}"
    if op in ("add_index", "remove_index"):
        index = diff[1]
        unique = " unique" if index.unique else ""
        return f"{_column_names(index)}{unique}"
    if op in ("add_constraint", "remove_constraint", "add_fk", "remove_fk"):
        return _column_names(diff[1])
    if op.startswith("modify_"):
        return f"{diff[5]} -> {diff[6]}"
    return str(diff)[:120]


def measure(db_url: str) -> dict[str, Any]:
    """Compare ``SQLModel.metadata`` against a live database and report the diffs.

    Args:
        db_url: A *synchronous* SQLAlchemy URL for a database already at
            ``alembic upgrade head``.

    Returns:
        ``{"tables_in_metadata": int, "diffs": [{"fingerprint", "operation",
        "table", "object", "detail"}, …]}``.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    sqlmodel, include_object = _load_alembic_env()

    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"include_object": include_object}
            )
            raw = compare_metadata(context, sqlmodel.metadata)
    finally:
        engine.dispose()

    # ``modify_*`` diffs arrive as a list nested inside the result, so ``len()``
    # on the raw list under-counts. Flatten before doing anything with it --
    # docs/schema-drift-audit.md section 8 records this costing a 112-vs-113
    # discrepancy the first time drift was measured here.
    flat: list[tuple[Any, ...]] = []
    for entry in raw:
        if isinstance(entry, list):
            flat.extend(entry)
        else:
            flat.append(entry)

    diffs = []
    for diff in flat:
        operation, table, obj = _identify(diff)
        # ``#`` starts a note in the baseline file, so it must never appear
        # inside a fingerprint -- a server default containing one would
        # otherwise make the recorded line unmatchable.
        fingerprint = f"{operation} {table} {obj}".replace("#", "\\x23")
        diffs.append(
            {
                "fingerprint": fingerprint,
                "operation": operation,
                "table": table,
                "object": obj,
                "detail": _describe(diff),
            }
        )

    diffs.sort(key=lambda d: d["fingerprint"])
    return {"tables_in_metadata": len(sqlmodel.metadata.tables), "diffs": diffs}


if __name__ == "__main__":
    print(json.dumps(measure(sys.argv[1])))

"""Guard: alembic must see every SQLModel table that exists in ``src``.

A table-mapped class that ``alembic/env.py`` never imports is absent from
``SQLModel.metadata``. Autogenerate reads that absence as "the model was
deleted" and emits ``op.drop_table()`` against a table that is very much alive.
This has already happened here to two tables, one of them holding real rows.

The check runs in a fresh interpreter on purpose. ``SQLModel.metadata`` is
process-global, so by the time the rest of the suite has run, unrelated tests
have imported the entity modules themselves and the gap closes on its own —
the assertion would pass while autogenerate still misbehaves.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Runs in a subprocess: exec the model-import section of ``alembic/env.py``
# exactly as alembic would, then report what alembic ended up seeing.
#
# The expected set is *not* taken from ``import_all_entity_modules()``. Both
# sides coming from the same importer would make the comparison vacuous — it
# would only notice env.py dropping the call, not the discovery itself missing
# a model. ``discover_table_names()`` reads ``__tablename__`` literals out of
# the source text instead, which is an independent statement of the answer.
PROBE = """
import json, sys
from pathlib import Path

backend_root = Path(sys.argv[1])
sys.path.insert(0, str(backend_root))

env_source = (backend_root / "alembic" / "env.py").read_text(encoding="utf-8")
start = env_source.index("from sqlmodel import SQLModel")
end = env_source.index("target_metadata = SQLModel.metadata")
namespace = {}
exec(compile(env_source[start:end], "env_imports", "exec"), namespace)
SQLModel = namespace["SQLModel"]

from src.shared.model_registry import discover_entity_modules, discover_table_names

print(json.dumps({
    "seen_by_alembic": sorted(SQLModel.metadata.tables),
    "declared_in_source": sorted(discover_table_names()),
    "modules_with_tables": sorted(m for m, _ in discover_entity_modules()),
    "modules_imported": sorted(m for m, _ in discover_entity_modules() if m in sys.modules),
}))
"""


def _probe() -> dict[str, list[str]]:
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(BACKEND_ROOT)],
        capture_output=True,
        text=True,
        cwd=BACKEND_ROOT,
        check=False,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_alembic_env_registers_every_table_declared_in_src() -> None:
    """Every ``__tablename__`` in ``src`` reaches the metadata alembic compares."""
    probe = _probe()
    missing = sorted(set(probe["declared_in_source"]) - set(probe["seen_by_alembic"]))
    assert missing == [], (
        "alembic/env.py does not register these tables, so autogenerate would "
        f"emit drop_table() for them: {missing}"
    )


def test_alembic_env_imports_every_module_declaring_a_table() -> None:
    """The module-level view of the same invariant.

    Catches a module whose tables happen to be registered by some other import
    chain, which would leave the table-name check green while the entity module
    itself is still absent from what ``env.py`` deliberately loads.
    """
    probe = _probe()
    assert probe["modules_with_tables"], "table discovery found nothing at all"
    unimported = sorted(set(probe["modules_with_tables"]) - set(probe["modules_imported"]))
    assert unimported == [], f"alembic/env.py never imports these entity modules: {unimported}"

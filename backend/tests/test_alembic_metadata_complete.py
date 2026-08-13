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
# exactly as alembic would, snapshot the metadata, then import everything the
# source tree actually declares and report what appeared late.
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

seen_by_alembic = set(SQLModel.metadata.tables)

from src.shared.model_registry import import_all_entity_modules

import_all_entity_modules()
declared_in_src = set(SQLModel.metadata.tables)

print(json.dumps(sorted(declared_in_src - seen_by_alembic)))
"""


def test_alembic_env_imports_every_table_in_src() -> None:
    """Every ``table=True`` class in ``src`` is registered by ``alembic/env.py``."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(BACKEND_ROOT)],
        capture_output=True,
        text=True,
        cwd=BACKEND_ROOT,
        check=False,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"

    missing = json.loads(result.stdout.strip().splitlines()[-1])
    assert missing == [], (
        "alembic/env.py does not register these tables, so autogenerate would "
        f"emit drop_table() for them: {missing}"
    )

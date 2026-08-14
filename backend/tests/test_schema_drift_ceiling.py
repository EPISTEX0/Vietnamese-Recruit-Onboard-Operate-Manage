"""Guard: the SQLModel models may not drift any further from the migrated schema.

The failure this stops is silent by construction. Deleting one ``index=True``
line leaves every test green -- the query still returns the right rows, it just
stops using an index -- while ``alembic revision --autogenerate`` starts
proposing ``op.drop_index()`` for a real index the database has. That is exactly
how the 14 performance indexes closed in 7a6a821 went missing in the first
place, and nothing in the suite noticed for the months they were gone.

**Shape: a set of accepted diffs, not a count.** ``docs/schema-drift-audit.md``
section 7 proposed a ceiling on the *number* of diffs. A number is cheaper to
maintain and strictly worse to read: "65 > 64" tells whoever hits it nothing
about what moved, and it goes red just as loudly when someone *cleans up* drift
as when someone adds it. ``tests/schema_drift_baseline.txt`` records each
accepted diff by fingerprint instead, which costs the same to run and lets this
module say which object drifted and which way autogenerate would move it. The
asymmetry is deliberate:

* a fingerprint not in the baseline **fails** -- that is new drift;
* a baseline fingerprint that has disappeared only **warns** -- someone paid
  down drift, and a fence that blocks that is a fence people route around.

That asymmetry has a known cost, stated here rather than hidden: until the
warned-about line is deleted, re-introducing *that* exact diff is free. Nothing
can tell "cleaned up, baseline not yet updated" apart from "cleaned up and put
back" from a single measurement, and blocking the cleanup is the worse trade.
The warning names the lines to delete; deleting them closes the window.

**Cost: about 6s** (~5s for the container plus the migration chain, ~0.7s for
the probe), cheap enough to be an ordinary suite test. It also runs as its own
CI job (`Gate 6 - schema drift`). That job earned its keep back when Gate 4b was
`continue-on-error: true` and a fence living only in the suite could never fail a
build; 838a525 dropped that flag, so the suite is load-bearing again and the job
is kept for two reasons that survive the change. It needs its own container
anyway (see below), and it is the only gate that measures drift -- folded into
Gate 4b it would land behind ~2500 unrelated tests and read as "the backend
suite is red" rather than "the models drifted".

**Why its own container** rather than the session-scoped ``postgres_async_url``:
this fence has to compare the models against a database whose entire history is
the migration chain. The shared database is reachable by every other test, and
one stray ``CREATE TABLE`` left behind by a failing test elsewhere would surface
here as phantom drift. Five seconds buys a reading that cannot be polluted.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.postgres_support import make_postgres_container

# backend/ -- the directory holding alembic.ini and the alembic/ package.
BACKEND_DIR = Path(__file__).resolve().parents[1]
BASELINE_PATH = Path(__file__).parent / "schema_drift_baseline.txt"
BASELINE_REL = BASELINE_PATH.relative_to(BACKEND_DIR.parent)

# A vacuous green is the one outcome worse than a red: if the probe ever reports
# an almost-empty metadata, every accepted diff would read as "cleaned up" and
# every real one would vanish with it. The repository declared 48 tables when
# this guard was written.
MINIMUM_TABLES_IN_METADATA = 40

# What each operation would do to the *database* if someone ran autogenerate and
# applied the result. This is the part a bare diff count cannot tell you.
CONSEQUENCE = {
    "add_column": "would ADD this column to the database",
    "add_constraint": "would ADD this constraint to the database",
    "add_fk": "would ADD this foreign key to the database",
    "add_index": "would CREATE this index in the database",
    "add_table": "would CREATE this table in the database",
    "add_table_comment": "would set this table's comment",
    "remove_column": "would DROP this column -- DATA LOSS",
    "remove_constraint": "would DROP this constraint -- integrity loss",
    "remove_fk": "would DROP this foreign key -- integrity loss",
    "remove_index": "would DROP this index -- silent performance loss",
    "remove_table": "would DROP this table -- DATA LOSS",
    "remove_table_comment": "would clear this table's comment",
}


def _consequence(operation: str) -> str:
    """Describe what applying this diff would do, defaulting for ``modify_*``."""
    if operation in CONSEQUENCE:
        return CONSEQUENCE[operation]
    if operation.startswith("modify_"):
        return f"would ALTER this column ({operation.removeprefix('modify_')})"
    return "unrecognised operation -- read the diff by hand"


def _note(diff: dict[str, Any]) -> str:
    """Render the baseline note for a diff, or nothing when it would just repeat itself.

    ``modify_type`` fingerprints already carry ``TEXT -> VARCHAR(10)``; printing
    it again as a note is noise in the exact place a reader is trying to see
    what changed. Lines produced here are copy-pasteable into the baseline.
    """
    if diff["detail"] and diff["detail"] not in diff["fingerprint"]:
        return f"    # {diff['detail']}"
    return ""


def _run_alembic_upgrade_head(async_url: str) -> None:
    """Run ``alembic upgrade head`` against ``async_url`` using the real env.

    Same shape as ``tests/conftest.py``: ``env.py`` reads ``DATABASE_URL`` and
    builds its own async engine, so the variable is what actually points the
    migration at the container.
    """
    from alembic.config import Config

    from alembic import command

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", async_url)

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="module")
def drift_report() -> Iterator[dict[str, Any]]:
    """Migrate a private database to head and report the drift a fresh process sees."""
    docker = pytest.importorskip("docker")

    try:
        docker.from_env().ping()
    except Exception:  # noqa: BLE001 - any docker error means "not available"
        pytest.skip("Docker is not available for the schema drift guard")

    with make_postgres_container() as postgres:
        sync_url = postgres.get_connection_url()
        _run_alembic_upgrade_head(
            sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        )

        result = subprocess.run(
            [sys.executable, "-m", "tests.schema_drift_probe", sync_url],
            capture_output=True,
            text=True,
            cwd=BACKEND_DIR,
            check=False,
        )
        assert result.returncode == 0, f"drift probe failed:\n{result.stderr}"
        yield json.loads(result.stdout.strip().splitlines()[-1])


def _accepted_fingerprints() -> list[str]:
    """Read the baseline, dropping blank lines and the ``#`` notes."""
    accepted = []
    for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        fingerprint = line.split("#", 1)[0].strip()
        if fingerprint:
            accepted.append(fingerprint)
    return accepted


def test_baseline_file_is_readable() -> None:
    """The baseline is the whole comparison; an empty one would make the guard vacuous."""
    accepted = _accepted_fingerprints()
    assert len(accepted) > 1, f"{BASELINE_REL} holds no fingerprints"
    assert len(set(accepted)) == len(accepted), (
        f"{BASELINE_REL} has duplicate fingerprints: "
        f"{sorted({f for f in accepted if accepted.count(f) > 1})}"
    )


def test_probe_sees_the_whole_model(drift_report: dict[str, Any]) -> None:
    """Sanity check on the measurement itself, before anything is concluded from it."""
    tables = drift_report["tables_in_metadata"]
    assert tables >= MINIMUM_TABLES_IN_METADATA, (
        f"the drift probe only saw {tables} tables in SQLModel.metadata, below the "
        f"{MINIMUM_TABLES_IN_METADATA} this guard expects. The comparison below would "
        "be measuring almost nothing -- fix the probe before trusting its verdict."
    )


def test_no_new_drift_between_models_and_migrated_schema(
    drift_report: dict[str, Any],
) -> None:
    """No diff may appear that ``schema_drift_baseline.txt`` has not already accepted."""
    accepted = set(_accepted_fingerprints())
    new = [d for d in drift_report["diffs"] if d["fingerprint"] not in accepted]

    if not new:
        return

    lines = [
        f"The models drifted from the migrated schema: {len(new)} diff(s) that "
        f"{BASELINE_REL} does not accept.",
        "",
        "Each line is what `alembic revision --autogenerate` would now emit, and "
        "what applying it would do to the database:",
        "",
    ]
    for diff in new:
        lines.append(f"  {diff['fingerprint']}{_note(diff)}")
        lines.append(f"      autogenerate {_consequence(diff['operation'])}")
    lines += [
        "",
        "If a model change caused this, the model is probably the side that is wrong: "
        "the database is built by the migration chain and this ran against a database "
        "at `alembic upgrade head`.",
        "",
        f"If the diff is intentional and harmless, append it to {BASELINE_REL}:",
        "",
    ]
    lines += [f"  {diff['fingerprint']}{_note(diff)}" for diff in new]
    raise AssertionError("\n".join(lines))


def test_baseline_does_not_list_drift_that_is_already_gone(
    drift_report: dict[str, Any],
) -> None:
    """Cleaned-up drift warns instead of failing, so paying down debt is never blocked.

    A count-based ceiling cannot make this distinction -- it sees only that the
    number moved. Closing drift is the outcome this whole exercise wants, so it
    must not be the thing that turns CI red; the baseline going stale is a
    bookkeeping nit, and a warning is the right weight for it.
    """
    current = {d["fingerprint"] for d in drift_report["diffs"]}
    gone = [f for f in _accepted_fingerprints() if f not in current]

    if gone:
        listing = "\n".join(f"  {f}" for f in gone)
        warnings.warn(
            f"{len(gone)} accepted diff(s) are gone -- drift was cleaned up. Delete "
            f"these lines from {BASELINE_REL} so the guard keeps ratcheting:\n{listing}",
            UserWarning,
            stacklevel=1,
        )

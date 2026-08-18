"""Structural guard: every broad ``except`` in the guarded modules logs or re-raises.

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

The property this file enforces: **no ``except`` clause in a guarded module
that catches broadly (``Exception``, ``BaseException``, or bare ``except:``)
may swallow the exception without something outside the handler learning it
happened.** Concretely that is one of two things: a logging call at a level
visible by default (see below), or a ``raise`` that runs on every path
through the handler (see "Re-raise counts as non-silent" below).

"Logging call" is any method call of the form ``<name>.<method>(...)`` where
``<name>`` is a bare identifier containing ``log`` and ``<method>`` is one of
``warning``/``warn``/``error``/``exception``/``critical``/``log``
(``logger.exception(...)``, ``log.warning(...)``) -- not narrowed to
``logger.exception`` specifically. ``debug`` and ``info`` are deliberately
excluded: both are typically disabled by the root logger's default level in
production, so a handler that only calls ``logger.debug(...)`` before
swallowing is exactly the failure mode #375 exists to close -- the DB outage
happens and nothing visible is ever emitted. A handler must log at
``warning`` or above to satisfy this guard.

The receiver must be a bare name, not an attribute chain or a call result --
``self._logger.warning(...)`` and ``logging.getLogger(__name__).error(...)``
are deliberately not recognized. Every module in scope that logs uses the
same module-level ``logger = logging.getLogger(__name__)`` convention;
matching only that shape, honestly, beats a chain-walking matcher that
nothing here exercises and that risks false-accepting an unrelated call by
coincidence of substring. None of the thirteen files added for #386 use
``self._logger`` either -- checked before writing this, not assumed.

## Re-raise counts as non-silent

A handler that ``raise``s (bare ``raise`` or ``raise ... from ...``) instead
of swallowing satisfies the real property too -- the exception keeps
propagating, so it is never silent even with no log call of its own. #377's
census conflated "does the handler log" with the real property and
over-counted (51 vs. the true 33) because a chunk of the ``except
Exception`` handlers it flagged were `raise`-and-propagate, not swallow.

Earlier revisions of this guard did not check for a re-raise, because no
handler in ``context_builder.py`` or ``tool_registry.py`` re-raised --
verified while widening scope for #381: all six broad handlers in
``tool_registry.py`` return after logging, same shape as
``context_builder.py``'s nine. That earlier docstring flagged this as a
known, deliberate gap: latent, not live, but live the moment a future file
added to scope had a broad handler that re-raised. #386 is that file:
``gmail/application/classification_rollout.py``'s ``ClassificationRollout.
classify`` has a broad handler that records a ``RolloutTelemetryEvent`` and
then bare-``raise``s, with no log call of its own. Before this revision the
detector would have false-flagged it.

``_handler_raises_unconditionally`` only recognizes a ``raise`` that is
itself a **direct, top-level statement** in the handler's body -- not one
found anywhere via a recursive walk. A ``raise`` nested inside an ``if`` (or
``for``/``while``/``try``) only runs on the paths that reach it; the paths
that skip it still swallow silently, so treating any ``raise`` found
anywhere in the body as sufficient would false-negative on exactly that
shape. This choice is conservative in the safe direction: a handler with a
real but non-top-level unconditional raise (e.g. inside a ``with`` block
that always executes) is treated as silent and needs either a log line or an
allowlist entry, rather than the detector trusting a raise it can't prove
executes on every path. ``test_the_census_still_flags_a_conditional_raise_
as_silent`` is the regression test for this: it constructs a handler whose
only ``raise`` is inside an ``if`` and asserts the guard still flags it.

## Fifteen files, not all of ``src/``

#375's brief measured 60 silent broad-except handlers across ``src/`` and
explicitly scoped that ticket to the nine in ``context_builder.py`` -- "một
file, một hình thái". #381 widened scope to ``tool_registry.py`` (five
handlers that returned a false ``*_NOT_FOUND`` claim to the LLM for any
exception). #386 widened scope again, in three reviewed tiers -- C1 (three
``assistant/application`` files, same tool-call-failure shape as
``tool_registry.py``, merged `52af520`), C2 (three worker files, plus an
unrelated ``aclose()``-inside-``try`` connection leak fixed alongside,
merged `17577d2`), C3 (the remaining seven files, merged `11fc7f5`) -- to
the thirteen files below, each already reviewed and merged before this
guard widening landed. The other silent broad-except handlers scattered
across the rest of ``src/`` remain out of scope, tracked as separate
follow-up work (#377's PR groups B/C/D), not silently absorbed here.
Widening further would fail immediately on files nobody has reviewed yet and
turn a scoped fix into an unreviewed mass-change.

## Allowlist: legitimate silence, keyed to survive refactors

C2 and C3 concluded that a handful of broad handlers are *correctly* silent
-- multi-key decrypt retries, heartbeat writes that are already fail-visible
through a Redis TTL, a shadow-mode classifier failure that's already
recorded into telemetry the release gate reads. These are real findings
under the property above (no log, no re-raise) and need an explicit,
reviewable exemption rather than a weakened detector.

The allowlist is keyed by **(path, qualname of the enclosing function)**,
not by line number. A line-numbered entry rots silently: the function moves,
the entry still "works" (it still matches *some* line in the file), and now
exempts the wrong handler while the one it was written for goes unchecked.
Keying by qualname fails loudly instead -- rename or delete the function and
the entry points at nothing.

Each entry also carries ``expected_silent_count``: the number of silent
broad handlers the function is known to contain. Keying by qualname alone
would silently widen scope too: exempt ``AIClassifier._safe_model_dump`` for
its one known silent handler, and a second one added later to the same
function would ride in for free with no review. ``expected_silent_count``
turns the check into a comparison, not a lookup -- ``_validate_allowlist``
recounts the real number of silent handlers in the named function and
requires it to match exactly. Both failure modes have a dedicated synthetic
test rather than trusting ``_ALLOWLIST`` against the real files alone:
``test_allowlist_check_flags_a_dead_qualname`` (entry renamed/deleted --
must go red) and ``test_allowlist_check_flags_a_second_silent_handler_
added_to_an_exempted_function`` (count drifts -- must go red).

``AIClassifier._safe_model_dump`` is the one entry with
``expected_silent_count=2``: its ``model_dump()`` attempt and its ``.dict()``
attempt are two separate ``try``/``except`` blocks in the same function,
both part of one try-then-fallback chain, and share the same reasoning.

The ``reason`` text on each entry is taken from the inline comment C2/C3
already wrote at that handler, not re-derived -- keeping the two in sync is
a human responsibility this file cannot enforce, but starting from the same
words the original author used is the closest available proxy.

## Guarding against a xanh-rỗng extractor (lesson from #359/#360/#370)

``test_the_census_reports_a_known_positive`` and
``test_the_census_stays_quiet_when_the_handler_logs`` run the visitor over
two synthetic modules that differ in exactly one way: one broad handler logs
before returning, the other doesn't. Without the second, a visitor whose
extractor matched everything (e.g. flagging every ``except`` regardless of
whether it logs) would leave ``test_no_silent_broad_except_in_guarded_modules``
green today by coincidence, then still be green the day someone deletes a
``logger.exception(...)`` call by accident -- the whole point of this guard.
The re-raise and allowlist additions follow the same lesson: every new
capability (recognizing a re-raise, recognizing a legitimate exemption) gets
a paired test proving both that it fires when it should and that it doesn't
fire when it shouldn't.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# backend/
BACKEND_ROOT = Path(__file__).resolve().parent.parent
CONTEXT_BUILDER_PATH = BACKEND_ROOT / "src/modules/assistant/application/context_builder.py"
TOOL_REGISTRY_PATH = BACKEND_ROOT / "src/modules/assistant/application/tool_registry.py"
ASSISTANT_SERVICE_PATH = BACKEND_ROOT / "src/modules/assistant/application/assistant_service.py"
EMPLOYEE_ASSISTANT_SERVICE_PATH = (
    BACKEND_ROOT / "src/modules/assistant/application/employee_assistant_service.py"
)
STREAMING_LOOP_PATH = BACKEND_ROOT / "src/modules/assistant/application/streaming_loop.py"
GMAIL_WORKER_PATH = BACKEND_ROOT / "src/modules/gmail/worker.py"
KNOWLEDGE_BASE_WORKER_PATH = BACKEND_ROOT / "src/modules/knowledge_base/worker.py"
ONBOARDING_WORKER_PATH = BACKEND_ROOT / "src/modules/onboarding/worker.py"
RUNTIME_ROUTER_PATH = BACKEND_ROOT / "src/modules/recruitment/api/runtime_router.py"
LLM_ADAPTER_PATH = BACKEND_ROOT / "src/modules/recruitment/infrastructure/llm_adapter.py"
CLASSIFICATION_SERVICE_PATH = (
    BACKEND_ROOT / "src/modules/gmail/application/classification_service.py"
)
CLASSIFICATION_ROLLOUT_PATH = (
    BACKEND_ROOT / "src/modules/gmail/application/classification_rollout.py"
)
AI_CLASSIFIER_PATH = BACKEND_ROOT / "src/modules/gmail/infrastructure/ai_classifier.py"
CRYPTO_UTILS_PATH = BACKEND_ROOT / "src/modules/identity/infrastructure/crypto_utils.py"
EXCEL_PARSER_PATH = BACKEND_ROOT / "src/modules/employee/infrastructure/excel_parser.py"

TARGET_PATHS = (
    CONTEXT_BUILDER_PATH,
    TOOL_REGISTRY_PATH,
    ASSISTANT_SERVICE_PATH,
    EMPLOYEE_ASSISTANT_SERVICE_PATH,
    STREAMING_LOOP_PATH,
    GMAIL_WORKER_PATH,
    KNOWLEDGE_BASE_WORKER_PATH,
    ONBOARDING_WORKER_PATH,
    RUNTIME_ROUTER_PATH,
    LLM_ADAPTER_PATH,
    CLASSIFICATION_SERVICE_PATH,
    CLASSIFICATION_ROLLOUT_PATH,
    AI_CLASSIFIER_PATH,
    CRYPTO_UTILS_PATH,
    EXCEL_PARSER_PATH,
)

_BROAD_EXCEPTION_NAMES = frozenset({"Exception", "BaseException"})
# debug/info excluded on purpose: both are off by default in production, so a
# handler that only logs at one of those levels is still effectively silent.
_LOG_METHODS = frozenset({"warning", "warn", "error", "exception", "critical", "log"})


@dataclass(frozen=True)
class Finding:
    """One broad except handler that swallows without logging or re-raising."""

    path: Path
    lineno: int
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno}  {self.detail}"


@dataclass(frozen=True)
class AllowlistEntry:
    """A broad handler known to be legitimately silent, exempted by name, not by line."""

    path: Path
    qualname: str
    reason: str
    expected_silent_count: int


_ALLOWLIST: tuple[AllowlistEntry, ...] = (
    AllowlistEntry(
        path=GMAIL_WORKER_PATH,
        qualname="refresh_heartbeat",
        reason=(
            "Intentionally silent: runtime_router.py:108 reads this key. A failed "
            "set() just means the key isn't refreshed, so it expires after its "
            "existing ex=600 TTL and /runtime/health flips to unhealthy on its own "
            "-- the failure is already fail-visible without a log line here."
        ),
        expected_silent_count=1,
    ),
    AllowlistEntry(
        path=ONBOARDING_WORKER_PATH,
        qualname="refresh_heartbeat",
        reason=(
            "Intentionally silent: runtime_router.py:130 reads this key. A failed "
            "set() just means the key isn't refreshed, so it expires after its "
            "existing ex=600 TTL and /runtime/health flips to unhealthy on its own "
            "-- the failure is already fail-visible without a log line here."
        ),
        expected_silent_count=1,
    ),
    AllowlistEntry(
        path=CLASSIFICATION_ROLLOUT_PATH,
        qualname="ClassificationRollout.classify",
        reason=(
            "Intentionally silent here: candidate_provider_error is carried into "
            "the RolloutTelemetryEvent recorded below, persisted per-message by "
            "ClassificationRolloutRepository, and aggregated into "
            "provider_error_rate (classification_telemetry.py) which "
            "evaluate_release_gates() checks against _MAX_PROVIDER_ERROR_RATE "
            "before a candidate can be promoted out of shadow mode. A log line "
            "here would duplicate that audit trail without adding a channel "
            "anyone reads -- shadow-mode candidate failures are expected and "
            "never affect the production result (stable_result, selected below)."
        ),
        expected_silent_count=1,
    ),
    AllowlistEntry(
        path=AI_CLASSIFIER_PATH,
        qualname="AIClassifier._extract_content_from_response",
        reason=(
            "Intentionally silent: this try only wraps two logger.debug() calls "
            "made purely for diagnostics. Logging the failure of a debug log call "
            "would be circular, and nothing downstream depends on this block -- "
            "extraction continues unconditionally right below."
        ),
        expected_silent_count=1,
    ),
    AllowlistEntry(
        path=AI_CLASSIFIER_PATH,
        qualname="AIClassifier._safe_model_dump",
        reason=(
            "Intentionally silent: try-then-fallback chain -- model_dump() failing "
            "just means the response isn't a pydantic v2 model shape, the .dict() "
            "attempt is the documented fallback, and returning None at the end of "
            "the chain if neither works is the documented contract."
        ),
        expected_silent_count=2,
    ),
    AllowlistEntry(
        path=CRYPTO_UTILS_PATH,
        qualname="CryptoUtils._decrypt_with_available_keys",
        reason=(
            "Intentionally silent per-attempt: this loop tries every configured "
            "key (current, previous) x AAD combination, and `raise last` below "
            "re-raises the final attempt's exception once all combinations are "
            "exhausted. Logging here would fire on every legacy-format decrypt, "
            "since the legacy path always fails its first (AAD-bound) attempt "
            "before succeeding on the second -- that's normal operation, not an "
            "error to trace."
        ),
        expected_silent_count=1,
    ),
)


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


def _handler_raises_unconditionally(handler: ast.ExceptHandler) -> bool:
    """True if the handler has a top-level ``raise`` that runs on every path through it.

    Deliberately does not ``ast.walk`` the whole body: a ``raise`` nested inside
    an ``if`` (or ``for``/``while``/``try``) only propagates on the paths that
    reach it, so the handler can still swallow silently on the paths that skip
    it. Only a ``raise`` that is itself a direct statement in ``handler.body``
    is guaranteed to run every time the handler runs.
    """
    return any(isinstance(stmt, ast.Raise) for stmt in handler.body)


def _is_silent(handler: ast.ExceptHandler) -> bool:
    """True if nothing outside the handler can learn the exception happened."""
    return not _handler_logs(handler) and not _handler_raises_unconditionally(handler)


class _ScopeTracker(ast.NodeVisitor):
    """Walk one module, tagging each broad except handler with its enclosing def's qualname."""

    def __init__(self) -> None:
        self._scope: list[str] = []
        self.function_qualnames: set[str] = set()
        self.broad_handlers: list[tuple[str, ast.ExceptHandler]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def _visit_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scope.append(node.name)
        self.function_qualnames.add(".".join(self._scope))
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_def(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_def(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if _is_broad_handler(node):
            self.broad_handlers.append((".".join(self._scope), node))
        self.generic_visit(node)


def _broad_handlers(tree: ast.Module) -> list[ast.ExceptHandler]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and _is_broad_handler(node)
    ]


def _allowlist_index(
    entries: tuple[AllowlistEntry, ...],
) -> dict[tuple[Path, str], AllowlistEntry]:
    return {(entry.path, entry.qualname): entry for entry in entries}


def _scan(
    trees: dict[Path, ast.Module], allowlist: tuple[AllowlistEntry, ...] = ()
) -> list[Finding]:
    allowlisted = _allowlist_index(allowlist)
    findings: list[Finding] = []
    for path, tree in trees.items():
        tracker = _ScopeTracker()
        tracker.visit(tree)
        for qualname, handler in tracker.broad_handlers:
            if not _is_silent(handler):
                continue
            if (path, qualname) in allowlisted:
                continue
            findings.append(
                Finding(
                    path,
                    handler.lineno,
                    "broad except swallows without logging or re-raising -- add a "
                    "logger call (e.g. logger.exception(...)) before the return, or "
                    "add a reasoned _ALLOWLIST entry",
                )
            )
    findings.sort(key=lambda f: (str(f.path), f.lineno))
    return findings


def _validate_allowlist(
    entries: tuple[AllowlistEntry, ...], trees: dict[Path, ast.Module]
) -> list[str]:
    """Check every allowlist entry against the modules it claims to apply to.

    Returns one human-readable problem per entry that either names a qualname
    no longer present in its module, or whose declared
    ``expected_silent_count`` no longer matches the number of silent broad
    handlers actually in that function. An entry with no problems is
    trustworthy: the function it exempts still exists and still has exactly
    the handler count it was written for.
    """
    problems: list[str] = []
    for entry in entries:
        tree = trees.get(entry.path)
        if tree is None:
            problems.append(f"{entry.path}: allowlisted but not a scanned module")
            continue
        tracker = _ScopeTracker()
        tracker.visit(tree)
        if entry.qualname not in tracker.function_qualnames:
            problems.append(
                f"{entry.path}: allowlisted qualname {entry.qualname!r} no longer exists"
            )
            continue
        actual = sum(
            1
            for qualname, handler in tracker.broad_handlers
            if qualname == entry.qualname and _is_silent(handler)
        )
        if actual != entry.expected_silent_count:
            problems.append(
                f"{entry.path}: {entry.qualname} has {actual} silent broad handler(s), "
                f"allowlist declares {entry.expected_silent_count}"
            )
    return problems


@lru_cache(maxsize=1)
def _target_trees() -> dict[Path, ast.Module]:
    return {
        path: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in TARGET_PATHS
    }


def _synthetic(source: str) -> dict[Path, ast.Module]:
    return {CONTEXT_BUILDER_PATH: ast.parse(source)}


def test_the_scan_reaches_the_target_modules() -> None:
    """A wrong path or a broken extractor would make the guard below vacuously true."""
    trees = _target_trees()
    # Measured at the time of writing: context_builder.py=9, tool_registry.py=7
    # (drifted up from the 6 measured at #381; still above the existing floor),
    # assistant_service.py=2, employee_assistant_service.py=2,
    # streaming_loop.py=1, gmail/worker.py=4, knowledge_base/worker.py=2,
    # onboarding/worker.py=2, runtime_router.py=5, llm_adapter.py=1,
    # classification_service.py=7, classification_rollout.py=2,
    # ai_classifier.py=4, crypto_utils.py=1, excel_parser.py=1. Floors leave
    # headroom for ordinary growth while still catching a path pointed at the
    # wrong file or an extractor gone blind.
    floors = {
        CONTEXT_BUILDER_PATH: 7,
        TOOL_REGISTRY_PATH: 4,
        ASSISTANT_SERVICE_PATH: 1,
        EMPLOYEE_ASSISTANT_SERVICE_PATH: 1,
        STREAMING_LOOP_PATH: 0,
        GMAIL_WORKER_PATH: 2,
        KNOWLEDGE_BASE_WORKER_PATH: 1,
        ONBOARDING_WORKER_PATH: 1,
        RUNTIME_ROUTER_PATH: 3,
        LLM_ADAPTER_PATH: 0,
        CLASSIFICATION_SERVICE_PATH: 5,
        CLASSIFICATION_ROLLOUT_PATH: 1,
        AI_CLASSIFIER_PATH: 2,
        CRYPTO_UTILS_PATH: 0,
        EXCEL_PARSER_PATH: 0,
    }
    for path, floor in floors.items():
        handlers = _broad_handlers(trees[path])
        assert len(handlers) > floor, f"only {len(handlers)} broad except handlers found in {path}"


def test_no_silent_broad_except_in_guarded_modules() -> None:
    """No broad except handler in a guarded module swallows without logging or re-raising.

    Sanity floor: this must be 0, not the pre-fix counts measured across C1/C2/
    C3 (or the 9/5 measured before #375/#381) -- those numbers were the state
    *before* each fix, not a target to hold. The seven handlers C2/C3 judged
    legitimately silent are exempted via ``_ALLOWLIST``, not by weakening this
    assertion.
    """
    findings = _scan(_target_trees(), _ALLOWLIST)
    assert not findings, "Silent broad except (no log call, no re-raise) found:\n" + "\n".join(
        str(f) for f in findings
    )


def test_allowlist_entries_are_not_rotted() -> None:
    """Every _ALLOWLIST entry must still name a real function with the declared handler count."""
    problems = _validate_allowlist(_ALLOWLIST, _target_trees())
    assert not problems, "\n".join(problems)


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

_DEBUG_ONLY_MODULE = """
import logging

logger = logging.getLogger(__name__)


class ContextBuilder:
    async def _get_thing(self):
        try:
            return await self._repo.get_thing()
        except Exception:
            logger.debug("meh")
            return ""
"""

_BARE_RAISE_MODULE = """
import logging

logger = logging.getLogger(__name__)


class ContextBuilder:
    async def _get_thing(self):
        try:
            return await self._repo.get_thing()
        except Exception:
            self._telemetry.record_failure()
            raise
"""

_RAISE_FROM_MODULE = """
import logging

logger = logging.getLogger(__name__)


class ContextBuilder:
    async def _get_thing(self):
        try:
            return await self._repo.get_thing()
        except Exception as exc:
            self._telemetry.record_failure()
            raise RuntimeError("thing lookup failed") from exc
"""

_CONDITIONAL_RAISE_MODULE = """
import logging

logger = logging.getLogger(__name__)


class ContextBuilder:
    async def _get_thing(self):
        try:
            return await self._repo.get_thing()
        except Exception:
            if self._strict:
                raise
            return ""
"""

_TWO_SILENT_HANDLERS_IN_ONE_FUNCTION_MODULE = """
import logging

logger = logging.getLogger(__name__)


class ContextBuilder:
    async def _get_thing(self):
        try:
            return await self._repo.get_thing()
        except Exception:
            return ""
        try:
            return await self._fallback.get_thing()
        except Exception:
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


def test_the_census_reports_a_debug_only_handler_as_silent() -> None:
    """A handler that only logs at debug must still be caught.

    debug is off by default in production, so logger.debug(...) ahead of a
    swallow is no more visible than no log call at all -- if _LOG_METHODS
    were ever widened back to include it, this is what would go red.
    """
    findings = _scan(_synthetic(_DEBUG_ONLY_MODULE))

    assert len(findings) == 1, findings
    assert findings[0].lineno == 11, findings


def test_the_census_recognizes_a_bare_raise_as_non_silent() -> None:
    """A handler that re-raises is never a finding, even with no log call.

    This is the fix for the gap this file's docstring calls out: a handler
    that propagates the exception is not silent, whether or not it also logs.
    """
    findings = _scan(_synthetic(_BARE_RAISE_MODULE))
    assert not findings, findings


def test_the_census_recognizes_raise_from_as_non_silent() -> None:
    """``raise ... from exc`` propagates just like a bare ``raise`` -- also not a finding."""
    findings = _scan(_synthetic(_RAISE_FROM_MODULE))
    assert not findings, findings


def test_the_census_still_flags_a_conditional_raise_as_silent() -> None:
    """A ``raise`` nested inside an ``if`` must not exempt the handler.

    It does not run on every path -- the path that skips the ``if`` still
    swallows silently. This is the
    counterpart to the two re-raise tests above, proving the detector isn't
    accepting *any* raise anywhere in the body.
    """
    findings = _scan(_synthetic(_CONDITIONAL_RAISE_MODULE))
    assert len(findings) == 1, findings


def test_allowlist_check_flags_a_dead_qualname() -> None:
    """An allowlist entry naming a qualname that no longer exists must be caught."""
    trees = _synthetic(_SILENT_MODULE)
    entry = AllowlistEntry(
        path=CONTEXT_BUILDER_PATH,
        qualname="ContextBuilder._this_method_was_renamed",
        reason="synthetic",
        expected_silent_count=1,
    )
    problems = _validate_allowlist((entry,), trees)
    assert problems, "a dead allowlist qualname must be flagged"


def test_allowlist_check_flags_a_second_silent_handler_added_to_an_exempted_function() -> None:
    """A stale expected_silent_count must be caught if a handler is added to an exempted function.

    Otherwise the new handler rides in on the first one's exemption for free,
    with no review -- exactly the rot #386's brief warned this design has to
    resist.
    """
    trees = _synthetic(_TWO_SILENT_HANDLERS_IN_ONE_FUNCTION_MODULE)
    entry = AllowlistEntry(
        path=CONTEXT_BUILDER_PATH,
        qualname="ContextBuilder._get_thing",
        reason="synthetic",
        expected_silent_count=1,
    )
    problems = _validate_allowlist((entry,), trees)
    assert problems, "a second silent handler in an exempted function must be flagged"

"""Every narrowed `*Status` union in `frontend/lib/api/` must equal exactly
the value set of its backend source — no missing member (silently mapped to
a fallback UI branch), no extra member (a value that can never occur).

## Why this file exists on top of `test_status_meta_freshness.py`

That file already proves, transitively, that five unions
(`CandidateStatus`, `InboxStatus`, `JobOpeningStatus`, `CalendarConflictStatus`,
`ProcessingStatus`) equal their backend source: each backs a
`Record<Union, ...>` label map, TypeScript requires a `Record` over a union
of literal types to have *exactly* those keys (neither a subset nor a
superset compiles), and that file already checks map-keys-vs-backend. Union
== map keys (compiler-enforced) plus map keys == backend (that file) gives
union == backend for free. Five of #363's unions ride that transitivity and
are not repeated here.

The other unions this ticket touched have no `Record<Union, ...>` riding
alongside them, so nothing forces them into agreement with a source of
truth — which is exactly how #363's headline bug (`ConnectionStatus` in
`types.ts` missing `degraded`, the sanitize fallback
`organization_google_connection_service.py:134` returns on every unrecognized
DB value) survived. This file is the general form of the check
`test_status_meta_freshness.py` did only for `Record`-typed unions.

## Two backend source shapes

Most unions below compare against a real backend `StrEnum`/`str, Enum`
class, imported directly — the strongest source available. Two
(`RuntimeHealthStatus`, `ImportJobStatus`, `ImportCancelStatus`) have no
backend enum class at all; the backend value set only exists as a
docstring/comment (same "contract lives in a comment" shape
`test_status_meta_freshness.py` already documents for
`KnowledgeBaseDocument.status`). Those compare against a regex-extracted
value list instead — weaker (nothing stops the comment itself drifting from
the literals actually written), but still catches the frontend union
disagreeing with what the backend *says* it returns, which is the failure
mode #363 found.

## Unions with no backend source at all: `UNVERIFIABLE_UNIONS`

`EmailProcessingStatus` (`types.ts`) and `InterviewStatus` (`recruitment.ts`)
back raw `str` columns with *no* enum and *no* documented comment — their
value sets were established by grepping every literal write site by hand
(cited in the comment at each TS declaration). There is no automatable
source to diff against here; registering them in `UNVERIFIABLE_UNIONS` with
a reason is the allowlist this ticket's own guidance requires ("Allowlist
phải có lý do viết ngay cạnh trong code") rather than silently leaving them
off the discovery census.

## Guarding against a xanh-rỗng extractor

Every extractor has a known-positive test below. `_discover_status_union_names`
additionally guards the guard's own coverage: a new `export type FooStatus =
...` added to `lib/api/` that nobody registers (in `UNIONS` or
`UNVERIFIABLE_UNIONS`) fails `test_no_unregistered_union` — the same
"seventh map" defence `test_status_meta_freshness.py` already has for
`*_META` maps, generalized to unions.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import pytest

from src.modules.gmail.domain.enums import OutboundEmailStatus
from src.modules.identity.application.organization_google_connection_service import (
    VALID_CONNECTION_STATUSES,
)
from src.modules.onboarding.domain.enums import OnboardingStatus, OnboardingTaskStatus
from src.modules.payslip.domain.entities import PayslipStatus
from src.modules.recruitment.domain.enums import JobApplicationStatus, LinkProposalStatus

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_ROOT = REPO_ROOT / "frontend"
LIB_API_ROOT = FRONTEND_ROOT / "lib" / "api"

TYPES_TS = LIB_API_ROOT / "types.ts"
GMAIL_TS = LIB_API_ROOT / "gmail.ts"
RECRUITMENT_TS = LIB_API_ROOT / "recruitment.ts"
PAYSLIPS_TS = LIB_API_ROOT / "payslips.ts"
ONBOARDING_TS = LIB_API_ROOT / "onboarding.ts"
ADMIN_TS = LIB_API_ROOT / "admin.ts"

GMAIL_SCHEMAS = REPO_ROOT / "backend" / "src" / "modules" / "gmail" / "api" / "schemas.py"
RUNTIME_ROUTER = (
    REPO_ROOT / "backend" / "src" / "modules" / "recruitment" / "api" / "runtime_router.py"
)

# ---------------------------------------------------------------------------
# Extractors — frontend side
# ---------------------------------------------------------------------------


def _extract_ts_string_union(path: Path, type_name: str) -> set[str]:
    """Every string literal in `export type <type_name> = ...;`.

    Handles both the single-line (`= "a" | "b";`) and multi-line
    (`=\\n  | "a"\\n  | "b";`) forms this codebase writes — the body between
    `=` and the first `;` is captured whole (`re.DOTALL`) and every quoted
    literal inside it is taken, so line breaks and leading `|` don't matter.
    """
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf"\bexport type {re.escape(type_name)}\s*=\s*(.*?);",
        text,
        re.DOTALL,
    )
    assert match is not None, f"Không tìm thấy `export type {type_name} = ...;` trong {path}."
    values = set(re.findall(r'"([^"]*)"', match.group(1)))
    assert values, f"{type_name} trích ra 0 giá trị trong {path} — extractor có thể đã hỏng."
    return values


@cache
def _discover_status_union_names() -> frozenset[str]:
    """Every `export type *Status* = ...;` name declared under `lib/api/`.

    Scoped to `lib/api/*.ts` (not `.test.ts`), matching #363's own scope —
    this ticket is about the API-response type layer, not every `*Status`
    identifier in the app (e.g. inline literal unions on request params,
    already type-safe without a name, are out of scope by construction: this
    regex only matches a *named* `export type` declaration).
    """
    names: set[str] = set()
    for path in LIB_API_ROOT.glob("*.ts"):
        if ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        names.update(re.findall(r"\bexport type (\w*Status\w*)\s*=", text))
    return frozenset(names)


class TestFrontendExtractorSeesSomething:
    def test_known_positive_union_value(self) -> None:
        values = _extract_ts_string_union(RECRUITMENT_TS, "CandidateStatus")
        assert "new" in values

    def test_multiline_union_form(self) -> None:
        # ProcessingStatus is declared across 11 lines, one value per line —
        # the shape a single-line regex would miss.
        values = _extract_ts_string_union(RECRUITMENT_TS, "ProcessingStatus")
        assert len(values) == 11

    def test_raises_on_unknown_type_name(self) -> None:
        with pytest.raises(AssertionError, match="Không tìm thấy"):
            _extract_ts_string_union(RECRUITMENT_TS, "NoSuchStatusUnion")


class TestDiscoverySeesSomething:
    def test_known_positive_union_present(self) -> None:
        assert "CandidateStatus" in _discover_status_union_names()

    def test_dedup_left_exactly_one_outbound_email_status(self) -> None:
        # #363 removed the duplicate `OutboundEmailStatus` declaration in
        # recruitment.ts (it now imports the one in gmail.ts) — regression
        # guard for that specific cleanup, since `export type` grep would
        # not distinguish "declared once, imported elsewhere" from
        # "declared twice" on its own; this asserts the dedup by checking
        # gmail.ts is the only file whose text contains the declaration.
        declaring_files = [
            p.name
            for p in LIB_API_ROOT.glob("*.ts")
            if re.search(r"\bexport type OutboundEmailStatus\s*=", p.read_text(encoding="utf-8"))
        ]
        assert declaring_files == ["gmail.ts"]


# ---------------------------------------------------------------------------
# Extractors — backend side (comment-documented, no enum class)
# ---------------------------------------------------------------------------


def _extract_py_quoted_values(path: Path, anchor: str) -> set[str]:
    """Every quoted value on the same line as, and after, `anchor`.

    Backend has no enum class for these fields — the value set is only
    documented in a docstring/comment line. Matches both quote styles
    (`'x'` in docstrings, `"x"` in the `# ...` comment style) since this
    codebase uses both.
    """
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"{re.escape(anchor)}(.*)", text)
    assert match is not None, f"Không tìm thấy `{anchor}` trong {path}."
    values = set(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))
    assert values, f"0 giá trị trích được sau `{anchor}` trong {path} — extractor có thể đã hỏng."
    return values


class TestBackendCommentExtractorSeesSomething:
    def test_known_positive_import_job_status(self) -> None:
        values = _extract_py_quoted_values(GMAIL_SCHEMAS, "status: One of ")
        assert "running" in values
        assert len(values) == 5

    def test_known_positive_runtime_health_status(self) -> None:
        values = _extract_py_quoted_values(RUNTIME_ROUTER, "status: str  # ")
        assert values == {"healthy", "unhealthy", "degraded"}


# ---------------------------------------------------------------------------
# Bảng đối chiếu: union frontend ↔ nguồn sự thật backend
# ---------------------------------------------------------------------------

UNIONS: list[tuple[str, Path, set[str]]] = [
    ("OrganizationGoogleConnectionStatus", TYPES_TS, set(VALID_CONNECTION_STATUSES)),
    ("OutboundEmailStatus", GMAIL_TS, {m.value for m in OutboundEmailStatus}),
    ("LinkProposalStatus", RECRUITMENT_TS, {m.value for m in LinkProposalStatus}),
    ("JobApplicationStatus", RECRUITMENT_TS, {m.value for m in JobApplicationStatus}),
    ("PayslipStatus", PAYSLIPS_TS, {m.value for m in PayslipStatus}),
    ("OnboardingStatus", ONBOARDING_TS, {m.value for m in OnboardingStatus}),
    ("OnboardingTaskStatus", ONBOARDING_TS, {m.value for m in OnboardingTaskStatus}),
    (
        "RuntimeHealthStatus",
        ADMIN_TS,
        _extract_py_quoted_values(RUNTIME_ROUTER, "status: str  # "),
    ),
    (
        "ImportJobStatus",
        GMAIL_TS,
        _extract_py_quoted_values(GMAIL_SCHEMAS, "status: One of "),
    ),
    (
        "ImportCancelStatus",
        GMAIL_TS,
        _extract_py_quoted_values(GMAIL_SCHEMAS, "status: Result status ("),
    ),
]

REGISTERED_UNION_NAMES = {name for name, _, _ in UNIONS}

# Verified transitively by test_status_meta_freshness.py, not repeated here:
# each backs a `Record<Union, ...>` label map (TypeScript requires the exact
# key set for a Record over a union of literal types), and that file already
# checks the map's keys against this same backend source. See header comment.
TRANSITIVELY_VERIFIED_UNIONS: dict[str, str] = {
    "CandidateStatus": "CANDIDATE_STATUS_META in shared-ui.tsx",
    "InboxStatus": "INBOX_STATUS_META in shared-ui.tsx",
    "JobOpeningStatus": "JOB_STATUS_META in shared-ui.tsx",
    "ConflictStatus": "CONFLICT_STATUS_META in shared-ui.tsx",
    "ProcessingStatus": "PROC_STATUS_META in recruitment/review/page.tsx",
    "DocumentStatus": "DOCUMENT_STATUS_META in knowledge-base/page.tsx",
}

# Unions with no backend enum class and no documented comment to diff
# against — value set established once by hand, auditing every literal
# write site (cited at the TS declaration). See header comment.
UNVERIFIABLE_UNIONS: dict[str, str] = {
    "EmailProcessingStatus": (
        "EmailMessageEntity.processing_status (gmail/domain/entities.py:58) is a "
        "raw `str` column, no StrEnum. 8-value set audited by grep across "
        "classification_service.py and gmail/api/router.py — see the comment at "
        "the TS declaration in types.ts."
    ),
    "InterviewStatus": (
        "Interview.status (recruitment/domain/entities.py:361) is a raw `str` "
        "column, no StrEnum. 3-value set audited by grep across "
        "interview_scheduler_service.py and calendar_sync_service.py — see the "
        "comment at the TS declaration in recruitment.ts."
    ),
}


class TestStatusUnionMatchesBackend:
    def test_registered_unions_floor(self) -> None:
        # Measured at #363: 10 unions with a diffable backend source, plus 2
        # in UNVERIFIABLE_UNIONS with no such source (12 of the 13 narrowed
        # fields' unions; the 13th, JobApplicationInboxResult, shares
        # JobApplicationStatus with two other fields so is not a distinct
        # union). Floor sits just under that, per this repo's rule that a
        # measured count is a floor, not a target to hardcode.
        assert len(UNIONS) >= 9

    def test_no_unregistered_union(self) -> None:
        discovered = _discover_status_union_names()
        registered = (
            REGISTERED_UNION_NAMES | set(UNVERIFIABLE_UNIONS) | set(TRANSITIVELY_VERIFIED_UNIONS)
        )
        unregistered = discovered - registered
        assert unregistered == set(), (
            f"Union(s) {sorted(unregistered)} khớp `export type *Status* =` trong "
            "lib/api/ nhưng chưa đăng ký vào UNIONS, UNVERIFIABLE_UNIONS hoặc "
            "TRANSITIVELY_VERIFIED_UNIONS của test_status_union_freshness.py."
        )

    def test_unverifiable_allowlist_has_no_stale_entries(self) -> None:
        discovered = _discover_status_union_names()
        stale = (set(UNVERIFIABLE_UNIONS) | set(TRANSITIVELY_VERIFIED_UNIONS)) - discovered
        assert stale == set(), (
            f"UNVERIFIABLE_UNIONS/TRANSITIVELY_VERIFIED_UNIONS chứa {sorted(stale)} "
            "— không còn khớp một `export type *Status* =` thật nào trong "
            "lib/api/. Gỡ entry đã rữa."
        )

    def test_every_union_matches_its_backend_source(self) -> None:
        failures = []
        for name, path, backend_values in UNIONS:
            frontend_values = _extract_ts_string_union(path, name)
            missing = backend_values - frontend_values
            extra = frontend_values - backend_values
            if missing or extra:
                failures.append(
                    f"{name} ({path.name}): thiếu={sorted(missing)} thừa={sorted(extra)}"
                )
        assert not failures, "\n".join(failures)

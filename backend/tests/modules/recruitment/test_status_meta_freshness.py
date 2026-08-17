"""Mọi `*_STATUS_META` phía frontend phải có key đúng bằng tập giá trị backend
sinh ra cho field tương ứng — không thiếu (rơi vào fallback), không thừa
(bản dịch chết).

## Vì sao đọc *key của map*, không đọc union TypeScript

Ba trong sáu map dưới đây khai `Record<UnionType, …>` — TS đã ép map khớp
union đó tại compile time. Có thể coi việc đó là đủ và chỉ đối chiếu
union ↔ backend, để lại map↔union cho compiler lo. Bài học từ `PROC_STATUS_META`
(#356) là: điều đó vẫn không đủ. `PROC_STATUS_META: Record<ProcessingStatus, …>`
khớp `ProcessingStatus` phía frontend đủ 10/10 — TS xanh tuyệt đối — nhưng
`ProcessingStatus` phía backend (`enums.py`) có 11 giá trị; `ai_unavailable`
được ghi thật (`gmail/application/classification_service.py:283,298,318`) mà
frontend không có union member tương ứng. TS không thấy được backend, nên
không thể bắt lớp lỗi này dù map↔union hoàn hảo.

Đọc thẳng *key của map* (không phải union) khớp cả hai lớp lỗi trong một
đường kiểm: nó bằng union↔backend khi map đã được TS ép khớp union (ba map
`Record<Union, …>` dưới đây), và tự nó là map↔backend khi map chưa được ép
(`Record<string, …>` — đúng lỗ hổng khiến `CONFLICT_STATUS_META` rữa xuống
0/3 mà không ai thấy). Nó cũng là lưới an toàn nếu kiểu `Record<Union, …>`
của một map nào đó bị nới lỏng lại về `Record<string, …>` sau này — thứ mà
một guard chỉ đọc union sẽ không thấy.

## Hai known-positive độc lập (#356)

1. `CONFLICT_STATUS_META` — map↔union: khai `pending`/`resolved`, hai giá trị
   `CalendarConflict.status` (`entities.py`) chưa từng ghi. 0/3 trước khi sửa.
2. `ProcessingStatus`/`PROC_STATUS_META` — union↔backend: thiếu `ai_unavailable`.
   10/11 trước khi sửa.

## `DOCUMENT_STATUS_META` — không có enum backend

`KnowledgeBaseDocument.status` (và `EmployeeKnowledgeBaseDocument.status`,
`knowledge_base/domain/entities.py`) là `str` trần, hợp đồng nằm trong
`sa_column_kwargs={"comment": "pending | processing | ready | error"}` — cùng
bệnh "hợp đồng trong comment" mà #356 vừa đóng cho `CalendarConflict.status`
bằng cách thêm `CalendarConflictStatus`. Thêm một enum tương tự cho
`knowledge_base` là việc thật nhưng ngoài phạm vi #356 (module khác, ticket
không yêu cầu) — ghi nhận ở đây làm giới hạn có chép, không lẳng lặng bỏ map
này ra khỏi census. Guard đọc thẳng chuỗi trong comment thay vì import enum;
nếu ai sửa comment mà quên sửa migration/giá trị ghi thật, guard này không
biết — nó chỉ biết đối chiếu hai bên của cùng một broken contract.

## Chống xanh-rỗng

Mỗi extractor (ba phía frontend, hai phía backend) có một known-positive
riêng: nếu regex khớp sai (annotation kiểu, khối rỗng, ...), assertion đó
chết trước khi assertion so sánh chính kịp chạy vô nghĩa trên tập rỗng.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from src.modules.recruitment.domain.enums import (
    CalendarConflictStatus,
    CandidateStatus,
    InboxStatus,
    JobOpeningStatus,
    ProcessingStatus,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
FRONTEND_ROOT = REPO_ROOT / "frontend"

DASHBOARD_ROOT = FRONTEND_ROOT / "app" / "[locale]" / "(dashboard)"

SHARED_UI = FRONTEND_ROOT / "components" / "shared-ui.tsx"
REVIEW_PAGE = DASHBOARD_ROOT / "recruitment" / "review" / "page.tsx"
KNOWLEDGE_BASE_PAGE = DASHBOARD_ROOT / "knowledge-base" / "page.tsx"
KB_ENTITIES = (
    REPO_ROOT / "backend" / "src" / "modules" / "knowledge_base" / "domain" / "entities.py"
)

# ---------------------------------------------------------------------------
# Extractors — frontend side
# ---------------------------------------------------------------------------


def _extract_meta_keys(path: Path, map_name: str) -> list[str]:
    """Mọi key top-level trong khối `MAP_NAME: Record<...> = { ... };`.

    Không parse AST — mỗi entry trong sáu map này nằm gọn một dòng
    (`key: { label: ..., tone: ..., ... },`), nên khớp `^\\s+(\\w+):\\s*\\{`
    theo dòng là đủ và không cần một TS parser thật.
    """
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf"\b{re.escape(map_name)}\s*:\s*Record<.*?=\s*\{{\n(.*?)\n\}};",
        text,
        re.DOTALL,
    )
    assert match is not None, f"Không tìm thấy khối {map_name} trong {path}."
    keys = re.findall(r"^\s+(\w+):\s*\{", match.group(1), re.MULTILINE)
    assert keys, f"{map_name} trích ra 0 key — extractor có thể đã hỏng."
    return keys


@cache
def _discover_status_meta_map_names() -> frozenset[str]:
    """Mọi `*_META: Record<` trong frontend — không riêng sáu map đã biết.

    Đây là lưới bắt map thứ bảy: nếu ai thêm một `*_STATUS_META` mới mà
    quên đăng ký vào bảng `MAPS` dưới, tập này sẽ không còn bằng
    `{tên sáu map đã biết}` và bài test `test_no_unregistered_meta_map`
    (dưới) sẽ đỏ — thay vì một map không được bảo vệ mà không ai để ý.

    `@cache`: hai bài test dưới đây đều gọi hàm này, và nó quét ~120 file
    trong `frontend/` mỗi lần — không có lý do làm việc đó hai lần trong
    cùng một tiến trình test.
    """
    names: set[str] = set()
    paths = list(FRONTEND_ROOT.rglob("*.ts")) + list(FRONTEND_ROOT.rglob("*.tsx"))
    for path in paths:
        if "node_modules" in path.parts or ".next" in path.parts:
            continue
        if ".test." in path.name or path.name.endswith(".generated.ts"):
            continue
        text = path.read_text(encoding="utf-8")
        names.update(re.findall(r"\b(\w+_META)\s*:\s*Record<", text))
    return frozenset(names)


class TestFrontendExtractorSeesSomething:
    """known-positive cho `_extract_meta_keys`, trên map ổn định nhất (JOB)."""

    def test_known_positive_key_present(self) -> None:
        keys = _extract_meta_keys(SHARED_UI, "JOB_STATUS_META")
        assert "open" in keys

    def test_floor_on_key_count(self) -> None:
        keys = _extract_meta_keys(SHARED_UI, "JOB_STATUS_META")
        assert len(keys) >= 4


class TestDiscoverySeesSomething:
    def test_known_positive_map_present(self) -> None:
        assert "CANDIDATE_STATUS_META" in _discover_status_meta_map_names()


# ---------------------------------------------------------------------------
# Extractors — backend side
# ---------------------------------------------------------------------------


def _extract_kb_document_status_values() -> list[str]:
    """Giá trị hợp lệ của `KnowledgeBaseDocument.status`, đọc từ comment.

    Không có enum backend cho field này (xem docstring module). Cả hai bảng
    KB (HR + Employee) khai cùng một comment; assert bằng nhau ngay tại đây
    để một bảng đổi mà bảng kia quên đổi cũng làm test này đỏ, không chỉ
    guard so sánh với frontend.
    """
    text = KB_ENTITIES.read_text(encoding="utf-8")
    pattern = re.compile(
        r'status:\s*str\s*=\s*Field\(\s*default="pending",\s*max_length=20,\s*'
        r'sa_column_kwargs=\{"comment":\s*"([^"]+)"\},?\s*\)'
    )
    matches = pattern.findall(text)
    assert len(matches) == 2, (
        f"Kỳ vọng đúng 2 field status (KnowledgeBaseDocument + "
        f"EmployeeKnowledgeBaseDocument) khai comment 'pending | processing | "
        f"ready | error' trong {KB_ENTITIES} — tìm thấy {len(matches)}. Field đổi "
        "hình dạng (comment/formatting) thì sửa regex này, đừng để nó im lặng "
        "trích 0."
    )
    assert matches[0] == matches[1], (
        "KnowledgeBaseDocument.status và EmployeeKnowledgeBaseDocument.status "
        f"khai hai comment khác nhau: {matches[0]!r} vs {matches[1]!r}."
    )
    values = [v.strip() for v in matches[0].split("|")]
    assert values, "Comment rỗng — extractor có thể đã hỏng."
    return values


class TestBackendExtractorSeesSomething:
    def test_known_positive_kb_document_status(self) -> None:
        assert "pending" in _extract_kb_document_status_values()


# ---------------------------------------------------------------------------
# Bảng đối chiếu: map frontend ↔ nguồn sự thật backend
# ---------------------------------------------------------------------------

MAPS: list[tuple[str, Path, str, set[str]]] = [
    ("CANDIDATE_STATUS_META", SHARED_UI, "CandidateStatus", {m.value for m in CandidateStatus}),
    ("INBOX_STATUS_META", SHARED_UI, "InboxStatus", {m.value for m in InboxStatus}),
    ("JOB_STATUS_META", SHARED_UI, "JobOpeningStatus", {m.value for m in JobOpeningStatus}),
    (
        "CONFLICT_STATUS_META",
        SHARED_UI,
        "CalendarConflictStatus",
        {m.value for m in CalendarConflictStatus},
    ),
    ("PROC_STATUS_META", REVIEW_PAGE, "ProcessingStatus", {m.value for m in ProcessingStatus}),
    (
        "DOCUMENT_STATUS_META",
        KNOWLEDGE_BASE_PAGE,
        "KnowledgeBaseDocument.status (comment)",
        set(_extract_kb_document_status_values()),
    ),
]

REGISTERED_MAP_NAMES = {name for name, _, _, _ in MAPS}


class TestStatusMetaMatchesBackend:
    def test_registered_maps_floor(self) -> None:
        # Đo thật ở #356: sáu map, tổng 32 key (6+4+4+3+11+4). Đặt sàn ngay
        # dưới — số đo trong ticket rữa lặng lẽ, đừng chép số cứng ở đây nữa
        # khi map thứ bảy xuất hiện, cứ đo lại.
        assert len(MAPS) >= 6
        total_keys = sum(len(_extract_meta_keys(path, name)) for name, path, _, _ in MAPS)
        assert total_keys >= 30

    def test_no_unregistered_meta_map(self) -> None:
        """Một map mới khớp `*_META: Record<` mà không có trong `MAPS` ở trên
        là một map không được guard này bảo vệ — đỏ tại đây, không phải một
        guard ngắn hơn lặng lẽ bỏ qua nó.
        """
        discovered = _discover_status_meta_map_names()
        unregistered = discovered - REGISTERED_MAP_NAMES
        assert unregistered == set(), (
            f"Map(s) {sorted(unregistered)} khớp quy ước `*_META: Record<` nhưng "
            "chưa có trong bảng MAPS của test_status_meta_freshness.py. Thêm "
            "nguồn sự thật backend cho nó rồi đăng ký vào MAPS."
        )

    def test_every_map_matches_its_backend_source(self) -> None:
        failures = []
        for map_name, path, source_name, backend_values in MAPS:
            frontend_keys = set(_extract_meta_keys(path, map_name))
            missing = backend_values - frontend_keys
            extra = frontend_keys - backend_values
            if missing or extra:
                failures.append(
                    f"{map_name} ({path.name}) vs {source_name}: "
                    f"thiếu={sorted(missing)} thừa={sorted(extra)}"
                )
        assert not failures, "\n".join(failures)

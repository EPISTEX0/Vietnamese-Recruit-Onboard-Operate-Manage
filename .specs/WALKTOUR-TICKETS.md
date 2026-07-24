# Walkthrough — Ticket Breakdown

> Generated from `.specs/WALKTOUR-SPEC.md`
> Dependencies: T1 → T3, T2 → T3 (T1 và T2 làm song song)

---

## T1: Dọn dẹp Guide cũ

**Files xoá:**
- `frontend/components/guide-widget.tsx`
- `frontend/app/[locale]/(dashboard)/guide/` (cả thư mục)
- `frontend/lib/api/guide.ts`
- `backend/src/modules/identity/api/guide_router.py`
- `backend/src/modules/identity/api/guide_schemas.py`
- `backend/src/modules/identity/application/guide_service.py`

**Files sửa:**
- `backend/src/main.py` — xoá import `guide_router` + `app.include_router(guide_router)`
- `frontend/messages/vi.json` — xoá toàn bộ key `"guide"` block
- `frontend/messages/en.json` — xoá toàn bộ key `"guide"` block
- `frontend/app/[locale]/(dashboard)/layout.tsx` — xoá import `<GuideWidget />` và `<GuideWidget>` trong JSX

**Không xoá:**
- Cột `guide_progress` trong DB — keep (không migration backward)
- Migration file `081_...` — keep (lịch sử)
- ADR 0008 — archive nhưng không xoá

**Blocking:** none
**Depended by:** T3

---

## T2: Xây Walktour component + steps

**Cài đặt:**
- `npm install shepherd.js` trong `frontend/`
- Import Shepherd types

**Tạo mới:**
- `frontend/components/walktour-steps.ts`
  - 7 step definitions theo spec
  - Target selectors: `.walktour-brand`, `.walktour-nav-dashboard`, `.walktour-nav-recruitment`, `.walktour-nav-employees`, `.walktour-nav-attendance`, `.walktour-nav-system`, `.walktour-assistant`
  - Placement: `right` cho tất cả
  - Step texts tiếng Việt

- `frontend/components/walktour.tsx`
  - Shepherd Tour instance
  - `start()` method
  - Props: `autoStart?: boolean`
  - localStorage check: `vroom_walkthrough_seen`
  - Nếu `autoStart && !localStorage.getItem('vroom_walkthrough_seen')` → auto-start
  - Khi hoàn thành → set localStorage
  - Nút skip → set localStorage + cancel
  - Progress "3/7" trong tooltip

- Message keys trong i18n:
  - `vi.json`: `"walktour"` block với 7 step titles + descriptions
  - `en.json`: `"walktour"` block (English fallback)

**Blocking:** none
**Depended by:** T3

---

## T3: Tích hợp Walktour vào Dashboard

**Files sửa:**
- `frontend/app/[locale]/(dashboard)/layout.tsx`
  - Import `<Walktour />`
  - Render `<Walktour />` ở đâu đó trong layout (hidden, chỉ trigger khi cần)
  - Thêm nút replay (icon `HelpCircle`) trong `topBarExtra`
  - Nút replay gọi `walktourRef.start()`
  - Auto-start logic: dùng `useEffect` check `isLoading` của các query chính (metrics, health, audit) đều `false` → mới trigger auto-start

- `frontend/components/app-shell.tsx`
  - Nếu cần thêm prop `onTourTrigger?: () => void` để walktour ref từ layout

- `frontend/messages/vi.json` + `en.json`
  - Thêm key `walktour` đã tạo ở T2 (hoặc merge vào T2)

**Blocking:** depends on T1, T2
**Depended by:** none

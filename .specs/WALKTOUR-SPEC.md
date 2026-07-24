# Walkthrough — Spotlight Overlay

> Thay thế "Hướng dẫn nhanh" (Guide Widget) bằng Walkthrough spotlight overlay dùng Shepherd.js.

## Trạng thái

spec-approved

## Quyết định kiến trúc

- **Thư viện**: Shepherd.js (`shepherd.js` npm package)
- **State**: localStorage (`vroom_walkthrough_seen`) — không cần backend
- **Target bằng**: class name / data attribute để tránh phụ thuộc text
- **Tooltip placement**: right (bên phải element)

## Trigger

1. **Auto-start**: Khi user vào Dashboard lần đầu sau First-Run Setup → chờ tất cả query (metrics, health, audit) load xong → mới show tour
2. **Replay**: Nút "Hướng dẫn" (icon `HelpCircle`) trong top-bar, bên cạnh settings

## Steps (7 step)

| # | Target selector | Spotlight | Nội dung tooltip |
|---|---|---|---|
| 1 | `.walktour-brand` (div chứa logo VR + text) | Logo + brand | "Chào mừng bạn đến với Vroom HR — hệ thống quản trị nhân sự toàn diện dành cho doanh nghiệp Việt Nam." |
| 2 | `.walktour-nav-dashboard` (Dashboard nav item) | Dashboard | "Dashboard tổng quan: xem metrics tuyển dụng, tình trạng hệ thống, và nhật ký hoạt động gần đây." |
| 3 | `.walktour-nav-recruitment` (Tuyển dụng section) | Recruitment group | "Tuyển dụng: Inbox nhận email ứng viên tự động, quản lý hồ sơ, Job Openings, lịch phỏng vấn và báo cáo metrics." |
| 4 | `.walktour-nav-employees` (Nhân sự section) | Employees group | "Nhân sự: Onboarding nhân viên mới, danh sách nhân viên, và xử lý yêu cầu từ nhân viên." |
| 5 | `.walktour-nav-attendance` (Chấm công section) | Attendance group | "Chấm công & Bảng lương: theo dõi giờ làm, quản lý chấm công và tạo phiếu lương cho nhân viên." |
| 6 | `.walktour-nav-system` (Hệ thống section) | System group | "Hệ thống: Knowledge Base nội bộ cho nhân viên, kết nối Gmail tuyển dụng, và cài đặt tổ chức." |
| 7 | `.walktour-assistant` (AI Assistant button) | AI Assistant | "Trợ lý AI: hỏi đáp thông minh — nhân viên có thể hỏi về nội quy, chính sách; HR có thể tra cứu dữ liệu nhanh." |

## Files thay đổi

### Xoá

| File | Ghi chú |
|---|---|
| `frontend/components/guide-widget.tsx` | Xoá |
| `frontend/app/[locale]/(dashboard)/guide/page.tsx` | Xoá cả thư mục |
| `frontend/lib/api/guide.ts` | Xoá |
| `backend/src/modules/identity/api/guide_router.py` | Xoá |
| `backend/src/modules/identity/api/guide_schemas.py` | Xoá |
| `backend/src/modules/identity/application/guide_service.py` | Xoá |

### Sửa

| File | Sửa |
|---|---|
| `backend/src/main.py` | Xoá import + include router của guide |
| `frontend/messages/vi.json` | Xoá key "guide", thêm "walktour" |
| `frontend/messages/en.json` | Xoá key "guide", thêm "walktour" |
| `frontend/app/[locale]/(dashboard)/layout.tsx` | Bỏ `<GuideWidget />`, thêm `<Walktour />`, thêm nút replay |

### Tạo mới

| File | Mô tả |
|---|---|
| `frontend/components/walktour.tsx` | Shepherd-based walkthrough component |
| `frontend/components/walktour-steps.ts` | Định nghĩa 7 step |

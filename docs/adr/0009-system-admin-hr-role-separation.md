# 9. Phân tách vai trò System Admin (Deployer) và HR (Nghiệp vụ HR)

* Status: accepted
* Deciders: Core Architecture Team
* Date: 2026-08-01

## Context and Problem Statement

Trong mô hình ban đầu của Vroom HR, hệ thống chỉ sử dụng 2 giá trị vai trò trong `UserRole` enum: `ADMIN` ("admin") và `USER` ("user").
Thực tế triển khai cho thấy vai trò `ADMIN` bị quá tải và gộp chung hai nhóm trách nhiệm hoàn toàn khác nhau:
1. **System Admin (Deployer / Quản trị hệ thống)**: Người chịu trách nhiệm First-Run Setup, cấu hình hạ tầng, kết nối Google Workspace OAuth Client ID/Secret, quản lý LLM Providers/API Keys và System Audit Logs.
2. **HR (Quản trị nghiệp vụ HR)**: Người tập trung vào hoạt động nhân sự hằng ngày (Tuyển dụng Candidate, Onboarding, Hồ sơ nhân viên, Chấm công, Đơn từ, Bảng lương, Knowledge Base và sử dụng HR AI Assistant).

Sự pha trộn này dẫn tới rủi ro bảo mật (nhân viên HR có thể tiếp xúc hoặc chỉnh sửa nhầm các key hạ tầng nhạy cảm) và không phản ánh đúng nguyên tắc đặc quyền tối thiểu (Principle of Least Privilege).

## Decision Drivers

- **Phân định ranh giới trách nhiệm**: Tách biệt hoàn toàn quản trị kỹ thuật hạ tầng và quản trị nghiệp vụ nhân sự.
- **Bảo mật dữ liệu nhạy cảm**: System Admin không được truy cập dữ liệu nhân sự (Candidate, Payslip, Employee record), HR không được truy cập secret kỹ thuật (OAuth Secret, LLM API Key).
- **Tương thích ngược**: Có lộ trình migration rõ ràng cho các deployment đang chạy với vai trò `ADMIN` cũ.

## Considered Options

1. **Giữ nguyên 2 role, bổ sung Fine-grained Permission System (RBAC Complex Matrix)**.
2. **Tách thành 3 vai trò rõ ràng (`SYSTEM_ADMIN`, `HR`, `USER`) cấp Enum và phân tách API Router Prefixes**.

## Decision Outcome

Lựa chọn **Option 2**: Tách thành 3 vai trò cấp hệ thống và áp dụng chính sách cách ly nghiêm ngặt (Strict Isolation Policy).

### 1. Mô hình vai trò (User Role Model)

Enum `UserRole` tại `src/modules/identity/domain/entities.py` được mở rộng thành:
- **`SYSTEM_ADMIN`** (`system_admin`): Quản trị viên kỹ thuật / Người triển khai (Deployer).
- **`HR`** (`hr`): Quản trị viên nghiệp vụ nhân sự.
- **`USER`** (`user`): Nhân viên công ty (Employee Self-Service).

### 2. Cấu trúc API Router & Access Control

Phân tách các endpoint thành 2 namespace chính:
- **`/api/system-admin/*`**: Gated bởi `require_system_admin`. Bao gồm: AI Config (`/ai-config/*`), OAuth Config (`/oauth/config`), System Audit Logs (`/audit-logs`), User/Role Management (`/users`), Org Domains (`/organization/domains`), Assistant Tools (`/assistant-tools`), Runtime Health (`/runtime/health`).
- **`/api/hr/*`**: Gated bởi `require_hr`. Bao gồm: Recruitment (`/recruitment/*`), Onboarding (`/onboarding/*`), Employees (`/employees/*`), Attendance (`/attendance/*`), Employee Requests (`/employee-requests/*`), Payslips (`/payslips/*`), Knowledge Base (`/knowledge-base/*`), HR Assistant (`/assistant/*`).

> **Đã thay đổi.** `AI Config` (`/ai-config/*`) ở trên không còn nguyên khối dưới `/api/system-admin/*` — xem [ADR 0018](./0018-organization-ai-configuration-ownership-split.md). Phần credential (13 route: `GET/PUT ai-config` + alias `ai-configuration`, `POST test`, `PUT source`, `PUT provider`, `POST activate-key`, `POST revoke-key`, `POST test-deployment-key`, 4 route `classification-rollout/*`) ở lại `/api/system-admin/organization/ai-config/*`. Phần consent và công tắc (9 route: `accept-data-policy`, `GET data-policy`, `automation/consent`, `assistant/consent`, `automation/enable|disable`, `assistant/enable|disable`, `policy-preset`) cộng 2 route đọc mới, hẹp và không có credential (`GET ""`, `GET provider-status`) đã chuyển hẳn sang `/api/hr/organization/ai-config/*`, gác `require_hr` — không còn nhân đôi ở namespace cũ. `Whitelist` (`/whitelist`) đã bị xoá hoàn toàn cả cơ chế lẫn route ở #418; không route nào thay thế nó ở bất kỳ namespace nào.

**Chính sách cách ly (Strict Block)**:
- `SYSTEM_ADMIN` truy cập bất kỳ endpoint nào thuộc `/api/hr/*` sẽ bị từ chối với HTTP 403 Forbidden (`HR_ACCESS_DENIED`).
- `HR` truy cập bất kỳ endpoint nào thuộc `/api/system-admin/*` sẽ bị từ chối với HTTP 403 Forbidden (`SYSTEM_ADMIN_ACCESS_DENIED`).

### 3. Tái thiết kế First-Run Setup (ADR-0001 Refactor)

1. First-Run Setup (`/setup`) khởi tạo Organization và tài khoản **System Admin** đầu tiên.
2. System Admin đăng nhập, cấu hình thông số kỹ thuật tối thiểu (Google OAuth, LLM Provider Key).
3. System Admin khởi tạo tài khoản **HR** đầu tiên tại `/system-admin/users`.

> **Đã thay đổi.** Đường dẫn frontend `/system-admin/users` ở dòng trên không còn đúng — xem [ADR 0014](./0014-system-admin-console-navigation.md). System Admin Console giữ base path `/settings`, nên trang đó là `/settings/users`. Phần có giá trị của quyết định này — tách bạch hoàn toàn bề mặt System Admin và HR — vẫn nguyên, và được thực hiện bằng route group `(system-admin)` với guard riêng chứ không bằng chuỗi URL. Namespace **API** `/api/system-admin/*` ở §2 vẫn là namespace riêng của System Admin, nhưng nội dung liệt kê bên trong nó đã đổi kể từ [ADR 0018](./0018-organization-ai-configuration-ownership-split.md) — xem khối "Đã thay đổi" ngay trong §2.
4. Tài khoản HR đăng nhập lần đầu, đổi mật khẩu và tiếp quản toàn bộ giao diện quản trị HR.

### 4. Tách Quick-Start Guide (ADR-0008 Refactor)

- **System Admin Guide**: [ ] Cấu hình Google OAuth, [ ] Cài đặt AI Key, [ ] Tạo tài khoản HR ban đầu.
- **HR Essential Setup Guide**: [ ] Kết nối Organization Shared Google Account, [ ] Tạo Job Opening đầu tiên, [ ] Upload tài liệu vào Knowledge Base.

> **Đã thay đổi.** Cặp "ba/ba" ở trên không còn đúng kể từ [ADR 0018](./0018-organization-ai-configuration-ownership-split.md). System Admin vẫn ba task, nhưng "Cài đặt AI Key" đổi nghĩa thành "cấu hình nhà cung cấp AI" — chỉ credential, không còn phần consent/công tắc. HR lên **bốn** task: ba task cũ cộng "đồng ý chính sách dữ liệu và bật AI", đặt cuối danh sách vì phụ thuộc System Admin hoàn tất cấu hình provider trước. Đích đến của task này là `/settings/ai-config`, route group `(dashboard)/settings` — không phải `/settings/ai`, vốn vẫn là trang credential của System Admin.

### 5. Chiến lược Migration dữ liệu

- Migration script (Alembic) sẽ cập nhật toàn bộ `User.role = 'admin'` hiện có thành `User.role = 'hr'`.
- Hàm `_bootstrap_super_admin` lúc startup kiểm tra `AUTH_SUPER_ADMIN_EMAIL`: nếu được cấu hình, tự động đảm bảo tài khoản này nhận `UserRole.SYSTEM_ADMIN`.

## Positive Consequences

- Đảm bảo nguyên tắc đặc quyền tối thiểu (Least Privilege).
- Ngăn ngừa rủi ro rò rỉ hoặc chỉnh sửa nhầm cấu hình LLM Key / OAuth Client Secret.
- Kiến trúc API sáng tỏ, dễ bảo trì và mở rộng thêm các vai trò phụ trợ trong tương lai.

## Negative Consequences

- Yêu cầu cập nhật tất cả các FastAPI routers hiện tại để đổi prefix và dependencies.
- Frontend cần cập nhật lại cấu trúc điều hướng và phân quyền UI theo 2 không gian làm việc riêng (`/system-admin` vs `/hr`).

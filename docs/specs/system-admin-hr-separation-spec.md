# Implementation Spec: Phân tách vai trò System Admin (Deployer) và HR (Nghiệp vụ HR)

* Target Branch / PRD: `feature/system-admin-hr-separation`
* Baseline ADR: `docs/adr/0009-system-admin-hr-role-separation.md`
* Baseline Glossary: `CONTEXT.md`

## 1. Mạch thay đổi Backend (Python / FastAPI / SQLModel)

### 1.1 Data Model & Enums (`src/modules/identity/domain/entities.py`)
- Cập nhật `UserRole`:
  ```python
  class UserRole(str, Enum):
      SYSTEM_ADMIN = "system_admin"
      HR = "hr"
      USER = "user"
  ```
- Alembic Migration (`backend/alembic/versions/xxxx_split_system_admin_and_hr_roles.py`):
  - Migration script thực thi SQL UPDATE: `UPDATE users SET role = 'hr' WHERE role = 'admin'`.
  - Mở rộng độ dài column `role` trong table `users` (nếu cần).

### 1.2 Access Control Dependencies (`src/modules/identity/api/dependencies.py`)
- Định nghĩa 2 dependency mới:
  ```python
  def require_system_admin(current_user: User = Depends(get_current_user)) -> User:
      if current_user.role != UserRole.SYSTEM_ADMIN:
          raise HTTPException(
              status_code=403,
              detail={"code": "SYSTEM_ADMIN_ACCESS_DENIED", "message": "System Admin access required"}
          )
      return current_user

  def require_hr(current_user: User = Depends(get_current_user)) -> User:
      if current_user.role != UserRole.HR:
          raise HTTPException(
              status_code=403,
              detail={"code": "HR_ACCESS_DENIED", "message": "HR access required"}
          )
      return current_user
  ```
- Deprecate `require_admin` / `AdminUserDep` cũ (hoặc alias tạm thời nhưng khuyến khích thay thế triệt để).

### 1.3 System Admin API Routers (`/api/system-admin/*`)
Chuyển đổi prefix và dependency cho các router hạ tầng:
- `identity/api/admin_router.py` $\rightarrow$ đổi prefix thành `/api/system-admin` và bảo vệ bằng `require_system_admin`.
- `identity/api/router.py` (OAuth Google Workspace connection) $\rightarrow$ gán `require_system_admin` cho endpoint cấp Organization.
- `recruitment/api/runtime_router.py` $\rightarrow$ đổi prefix `/api/system-admin/runtime/health` bảo vệ bằng `require_system_admin`.

### 1.4 HR API Routers (`/api/hr/*`)
Chuyển đổi prefix và dependency cho các router nghiệp vụ HR:
- `recruitment/api/*` (Candidate, Job Opening, Inbox, Evaluation, Conflict) $\rightarrow$ đổi prefix `/api/hr/recruitment/*` bảo vệ bằng `require_hr`.
- `onboarding/api/router.py` $\rightarrow$ đổi prefix `/api/hr/onboarding/*` bảo vệ bằng `require_hr`.
- `employee/api/router.py` $\rightarrow$ đổi prefix `/api/hr/employees/*` bảo vệ bằng `require_hr`.
- `attendance/api/router.py` $\rightarrow$ đổi prefix `/api/hr/attendance/*` bảo vệ bằng `require_hr`.
- `employee_request/api/admin_router.py` $\rightarrow$ đổi prefix `/api/hr/employee-requests/*` bảo vệ bằng `require_hr`.
- `payslip/api/admin_router.py` $\rightarrow$ đổi prefix `/api/hr/payslips/*` bảo vệ bằng `require_hr`.
- `knowledge_base/api/router.py` $\rightarrow$ đổi prefix `/api/hr/knowledge-base/*` bảo vệ bằng `require_hr`.
- `assistant/api/router.py` $\rightarrow$ đổi prefix `/api/hr/assistant/*` bảo vệ bằng `require_hr`.

### 1.5 Bootstrapping & First-Run Setup
- `main.py:_bootstrap_super_admin`: Cập nhật logic tìm `AUTH_SUPER_ADMIN_EMAIL`, gán `role = UserRole.SYSTEM_ADMIN` thay vì `ADMIN`.
- `auth_service.py`: Cập nhật `/setup` (First-Run Setup) gán `role = UserRole.SYSTEM_ADMIN` cho tài khoản ban đầu.
- `role_service.py`: Thêm phương thức `create_hr_account` để System Admin khởi tạo tài khoản HR đầu tiên.

---

## 2. Mạch thay đổi Frontend (Next.js / TypeScript / React)

### 2.1 API Client Layer (`frontend/lib/api/`)
- Tách `admin.ts` thành 2 client riêng biệt: `system-admin.ts` (`/api/system-admin/*`) và `hr.ts` (`/api/hr/*`).
- Cập nhật Enum UserRole trong TypeScript: `export type UserRole = 'system_admin' | 'hr' | 'user';`.

### 2.2 Navigation & Routing (`frontend/app/` & Navigation Layout)
- Tách không gian UI thành 2 layout riêng:
  - `/system-admin/*`: Dashboard hạ tầng, OAuth, AI Config, Whitelist, User/Role Management.
  - `/hr/*`: Dashboard tuyển dụng, Onboarding, Hồ sơ nhân viên, Chấm công, Bảng lương, Knowledge Base, HR Assistant.
- Cập nhật Middleware & Auth Context để redirect user dựa trên `role`:
  - `system_admin` $\rightarrow$ `/system-admin`
  - `hr` $\rightarrow$ `/hr`
  - `user` $\rightarrow$ `/ess` (Employee Self-Service)

---

## 3. Kế hoạch Kiểm thử & Xác minh (Verification Criteria)

1. **Pytest Unit/Integration Tests**:
   - Chạy test suite `pytest tests/` đảm bảo các endpoint `/api/system-admin/*` từ chối người dùng có role `hr` (HTTP 403 `SYSTEM_ADMIN_ACCESS_DENIED`).
   - Đảm bảo các endpoint `/api/hr/*` từ chối người dùng có role `system_admin` (HTTP 403 `HR_ACCESS_DENIED`).
2. **First-Run Setup Test**:
   - Chạy test flow `/setup` tạo `system_admin` $\rightarrow$ tạo `hr` account $\rightarrow$ HR login thành công.
3. **Database Migration Test**:
   - Chạy Alembic upgrade head kiểm tra migration data `admin` $\rightarrow$ `hr`.
